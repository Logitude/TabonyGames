from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.urls import reverse
from django.contrib.sites.models import Site
from django.core.mail import send_mail
from django.db.models.functions import Now
from django.utils.timezone import make_aware
from django.apps import apps

from users.models import User, get_deleted_user
from .models import Match, MatchPlayer, NationsChat

from . import nations

import asyncio
import json
import datetime
import threading
import queue

class TerminatePlay(Exception):
    pass

class NationsConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope['user']
        self.user_group_name = None
        if user.is_authenticated:
            user_id = user.pk
            self.user_group_name = f'nations_notifications_{user_id}'
            await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if self.user_group_name is not None:
            await self.channel_layer.group_discard(self.user_group_name, self.channel_name)

    @database_sync_to_async
    def get_number_of_turns_from_db(self):
        user = self.scope['user']
        if not user.is_authenticated:
            return 0
        archive_threshold = make_aware(datetime.datetime.now() - datetime.timedelta(days=7))
        return len(Match.objects.filter(current_player=user, new_turn__gte=archive_threshold)) + len(MatchPlayer.objects.filter(player=user, accepted=False, match__new_turn__gte=archive_threshold))

    async def new_turn(self, event):
        await self.send_turns_info()

    async def send_turns_info(self):
        number_of_turns = await self.get_number_of_turns_from_db()
        await self.send_json({'turns': number_of_turns})

    async def receive_json(self, content):
        user = self.scope['user']
        if not user.is_authenticated:
            return
        await self.send_turns_info()

class MatchInfo:
    def __init__(self, match_id):
        self.match_id = match_id
        self.player_count = None
        self.growth_resources = None
        self.extra_draft_nations = None
        self.resource_remainder_tiebreaker = None
        self.card_draw_limits = None
        self.weighted_card_draw = None
        self.korea_nerf = None
        self.lincoln_nerf = None
        self.players = None
        self.player_growth_resources = None
        self.replay = None
        self.prev_player = None
        self.current_player = None
        self.game_over = False
        self.log = None
        self.state = None

    async def rules(self):
        rules = {'growth_resources': self.growth_resources}
        if self.extra_draft_nations != 0:
            rules['extra_draft_nations'] = self.extra_draft_nations
        for house_rule in ('resource_remainder_tiebreaker', 'card_draw_limits', 'weighted_card_draw', 'korea_nerf', 'lincoln_nerf'):
            if getattr(self, house_rule):
                rules[house_rule] = True
        if self.growth_resources < 0:
            rules['player_growth_resources'] = self.player_growth_resources
        return rules

class ThreadState:
    def __init__(self):
        self.match_thread = None
        self.move_queue = None
        self.state_queue = None

    def is_running(self):
        return self.match_thread is not None and self.match_thread.is_alive()

class NationsMatchConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.match_info = MatchInfo(self.scope['url_route']['kwargs']['match_id'])
        self.thread_state = ThreadState()
        self.sent_initial_info = False
        self.avoid_duplicate_updates = False
        self.match_group_name = f'nations_match_{self.match_info.match_id}'
        await self.channel_layer.group_add(self.match_group_name, self.channel_name)
        user = self.scope['user']
        self.user_group_name = None
        if user.is_authenticated:
            user_id = user.pk
            self.user_group_name = f'nations_notifications_{user_id}'
            await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if self.thread_state.is_running():
            self.thread_state.move_queue.put(TerminatePlay)
        await self.channel_layer.group_discard(self.match_group_name, self.channel_name)
        if self.user_group_name is not None:
            await self.channel_layer.group_discard(self.user_group_name, self.channel_name)

    @database_sync_to_async
    def get_user_from_db(self, username):
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            user = get_deleted_user()
        return user

    @database_sync_to_async
    def get_match_from_db(self):
        try:
            match = Match.objects.get(match_id=self.match_info.match_id)
        except Match.DoesNotExist:
            return None
        return match

    @database_sync_to_async
    def get_match_and_players_from_db(self):
        try:
            match = Match.objects.get(match_id=self.match_info.match_id)
        except Match.DoesNotExist:
            return (None, None, None)
        match_players = match.players.all().order_by('pk')
        players = [match_player.player.username for match_player in match_players][:match.player_count]
        return (match, players, match.current_player.username)

    async def get_match(self):
        (match, players, current_player) = await self.get_match_and_players_from_db()
        self.match_info.player_count = match.player_count
        self.match_info.growth_resources = match.growth_resources
        self.match_info.extra_draft_nations = match.extra_draft_nations
        self.match_info.resource_remainder_tiebreaker = match.resource_remainder_tiebreaker
        self.match_info.card_draw_limits = match.card_draw_limits
        self.match_info.weighted_card_draw = match.weighted_card_draw
        self.match_info.korea_nerf = match.korea_nerf
        self.match_info.lincoln_nerf = match.lincoln_nerf
        self.match_info.players = players
        self.match_info.replay = match.replay.replace('\r', '').rstrip('\n')
        self.match_info.current_player = current_player
        self.match_info.game_over = match.game_over
        self.match_info.player_growth_resources = {player: await self.get_growth_resources_from_db(player) for player in players}

    @database_sync_to_async
    def save_match_to_db(self):
        try:
            match = Match.objects.get(match_id=self.match_info.match_id)
        except Match.DoesNotExist:
            return
        match.replay = self.match_info.replay + '\n'
        if self.match_info.game_over:
            user = get_deleted_user()
        else:
            try:
                user = User.objects.get(username=self.match_info.current_player)
            except User.DoesNotExist:
                user = get_deleted_user()
        match.current_player = user
        match.game_over = self.match_info.game_over
        if self.match_info.prev_player != self.match_info.current_player:
            match.new_turn = Now()
        match.save()

    async def save_match(self):
        await self.save_match_to_db()

    @database_sync_to_async
    def get_growth_resources_from_db(self, username):
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            return -1
        try:
            match = Match.objects.get(match_id=self.match_info.match_id)
        except Match.DoesNotExist:
            return -1
        try:
            match_player = match.players.get(player=user)
        except MatchPlayer.DoesNotExist:
            return -1
        return match_player.growth_resources

    @database_sync_to_async
    def get_accepted_players_from_db(self):
        try:
            match = Match.objects.get(match_id=self.match_info.match_id)
        except Match.DoesNotExist:
            return []
        return [player.player.username for player in match.players.filter(accepted=True)]

    async def has_accepted(self):
        if not self.scope['user'].is_authenticated:
            return False
        return self.scope['user'].username in (await self.get_accepted_players_from_db())

    @database_sync_to_async
    def add_player_to_match_db(self, match, player, growth_resources):
        try:
            match_player = match.players.get(player=player)
        except MatchPlayer.DoesNotExist:
            match_player = None
        if match_player is not None:
            match_player.growth_resources = growth_resources
            match_player.accepted = True
            match_player.save()
        else:
            MatchPlayer.objects.create(match=match, player=player, growth_resources=growth_resources, accepted=True)
        match.new_turn = Now()
        match.save()

    @database_sync_to_async
    def remove_player_from_match_db(self, match, player):
        try:
            match_player = match.players.get(player=player)
        except MatchPlayer.DoesNotExist:
            return
        match_player.delete()
        match.new_turn = Now()
        match.save()

    @database_sync_to_async
    def get_chat_log_from_db(self):
        try:
            match = Match.objects.get(match_id=self.match_info.match_id)
        except Match.DoesNotExist:
            return None
        user = self.scope['user']
        if user.is_authenticated:
            try:
                match_player = match.players.get(player=user)
            except MatchPlayer.DoesNotExist:
                match_player = None
        else:
            match_player = None
        chats = list(match.chats.all().order_by('pk'))
        if match_player is not None and chats:
            match_player.last_chat = chats[-1].pk
            match_player.save()
        chat_log = []
        for chat in chats:
            chat_log.append({'timestamp': chat.created.isoformat(), 'player': chat.player.username, 'message': chat.message})
        return chat_log

    @database_sync_to_async
    def save_chat_to_db(self, match, player, message):
        chat = NationsChat.objects.create(match=match, player=player, message=message)
        try:
            match_player = match.players.get(player=player)
        except MatchPlayer.DoesNotExist:
            match_player = None
        if match_player is not None:
            match_player.last_chat = chat.pk
            match_player.save()
        return chat

    @database_sync_to_async
    def get_notes_from_db(self):
        user = self.scope['user']
        if user.is_authenticated:
            try:
                match = Match.objects.get(match_id=self.match_info.match_id)
            except Match.DoesNotExist:
                match_player = None
            try:
                match_player = match.players.get(player=user)
            except MatchPlayer.DoesNotExist:
                match_player = None
        else:
            match_player = None
        if match_player is None:
            return ''
        return match_player.notes

    @database_sync_to_async
    def save_notes_to_db(self, notes):
        user = self.scope['user']
        if user.is_authenticated:
            try:
                match = Match.objects.get(match_id=self.match_info.match_id)
            except Match.DoesNotExist:
                match_player = None
            try:
                match_player = match.players.get(player=user)
            except MatchPlayer.DoesNotExist:
                match_player = None
        else:
            match_player = None
        if match_player is not None:
            match_player.notes = notes[:4000]
            match_player.save()

    @database_sync_to_async
    def get_number_of_turns_from_db(self):
        user = self.scope['user']
        if not user.is_authenticated:
            return 0
        archive_threshold = make_aware(datetime.datetime.now() - datetime.timedelta(days=7))
        return len(Match.objects.filter(current_player=user, new_turn__gte=archive_threshold)) + len(MatchPlayer.objects.filter(player=user, accepted=False, match__new_turn__gte=archive_threshold))

    def play_match(self):
        def report_state(nations_match):
            replay = nations_match.get_replay().replace('\r', '').rstrip('\n')
            log = nations_match.get_log().replace('\r', '').rstrip('\n')
            state = nations_match.get_state()
            self.thread_state.state_queue.put((replay, log, state))

        def move_getter(choice, options, undo):
            while True:
                report_state(nations_match)
                next_move = self.thread_state.move_queue.get()
                if next_move is TerminatePlay:
                    raise TerminatePlay()
                if next_move is not None:
                    break
            move_strings = [str(option) for option in options]
            if next_move in move_strings:
                move = options[move_strings.index(next_move)]
            else:
                move = next_move
            next_move = None
            return move

        nations_match = nations.Match(move_getter=move_getter, replay=self.match_info.replay)
        try:
            nations_match.play()
        except TerminatePlay:
            return
        except Exception:
            import traceback
            traceback.print_exc()
        while True:
            report_state(nations_match)
            next_move = self.thread_state.move_queue.get()
            if next_move is TerminatePlay:
                return

    async def get_match_info(self):
        if self.match_info.replay and self.match_info.state and self.thread_state.is_running():
            return
        await self.get_match()
        if not self.match_info.replay and len(await self.get_accepted_players_from_db()) == self.match_info.player_count:
            await self.create_match()
            await self.get_match()
        replay = self.match_info.replay
        state = self.match_info.state
        if (replay and not state) or (replay and state and not self.thread_state.is_running()):
            if not self.thread_state.is_running():
                self.thread_state.move_queue = queue.SimpleQueue()
                self.thread_state.state_queue = queue.SimpleQueue()
                self.thread_state.match_thread = threading.Thread(target=self.play_match)
                self.thread_state.match_thread.start()
            else:
                self.thread_state.move_queue.put(None)
            (self.match_info.replay, self.match_info.log, self.match_info.state) = self.thread_state.state_queue.get()
            self.match_info.current_player = self.match_info.state['next_move_player']
            self.match_info.game_over = self.match_info.state['game_over']

    async def create_match(self):
        def move_getter(choice, options, undo):
            raise TerminatePlay()
        rules = await self.match_info.rules()
        nations_match = nations.Match(player_names=self.match_info.players, move_getter=move_getter, rules=rules)
        try:
            nations_match.play()
        except TerminatePlay:
            pass
        self.match_info.replay = nations_match.get_replay().replace('\r', '').rstrip('\n')
        self.match_info.log = nations_match.get_log().replace('\r', '').rstrip('\n')
        self.match_info.state = nations_match.get_state()
        self.match_info.current_player = self.match_info.state['next_move_player']
        self.match_info.game_over = self.match_info.state['game_over']
        await self.save_match()
        current_player_user = await self.get_user_from_db(self.match_info.current_player)
        await self.channel_layer.group_send(f'nations_notifications_{current_player_user.pk}', {'type': 'new_turn'})
        event_loop = asyncio.get_event_loop()
        event_loop.create_task(self.notify(self.match_info.current_player))

    async def make_move(self, move):
        if not self.thread_state.is_running():
            await self.get_match_info()
        self.thread_state.move_queue.put(move)
        (self.match_info.replay, self.match_info.log, self.match_info.state) = self.thread_state.state_queue.get()
        self.match_info.prev_player = self.match_info.current_player
        self.match_info.current_player = self.match_info.state['next_move_player']
        self.match_info.game_over = self.match_info.state['game_over']

    async def received_info_request(self):
        if not self.sent_initial_info:
            await self.send_chat_log()
            await self.send_notes()
            await self.send_match_info()
        await self.send_turns_info()

    async def received_join(self, join_info):
        await self.get_match_info()
        user = self.scope['user']
        if not user.is_authenticated:
            return
        username = user.username
        is_superuser = self.scope['user'].is_superuser
        players = self.match_info.players
        player_count = self.match_info.player_count
        if is_superuser or (username in players and await self.has_accepted()) or (username not in players and len(players) == player_count):
            return
        growth_resources = self.match_info.growth_resources if self.match_info.growth_resources > 0 else join_info
        match = await self.get_match_from_db()
        await self.add_player_to_match_db(match, user, growth_resources)
        await self.send_match_info()
        self.avoid_duplicate_updates = True
        group_message = {'type': 'state_change_message', 'move': None}
        await self.channel_layer.group_send(self.match_group_name, group_message)
        await self.channel_layer.group_send(f'nations_notifications_{user.pk}', {'type': 'new_turn'})

    async def received_decline(self):
        match = await self.get_match_from_db()
        user = self.scope['user']
        if not user.is_authenticated:
            return
        await self.remove_player_from_match_db(match, user)
        await self.send_match_info()
        self.avoid_duplicate_updates = True
        group_message = {'type': 'state_change_message', 'move': None}
        await self.channel_layer.group_send(self.match_group_name, group_message)
        await self.channel_layer.group_send(f'nations_notifications_{user.pk}', {'type': 'new_turn'})

    async def received_move(self, move):
        await self.get_match_info()
        user = self.scope['user']
        if not user.is_authenticated:
            return
        username = user.username
        is_superuser = user.is_superuser
        if not self.match_info.game_over and (username == self.match_info.current_player or is_superuser):
            await self.make_move(move)
            await self.save_match()
            await self.send_match_info()
            self.avoid_duplicate_updates = True
            group_message = {'type': 'state_change_message', 'move': move}
            await self.channel_layer.group_send(self.match_group_name, group_message)
            if self.match_info.prev_player != self.match_info.current_player:
                prev_player_user = await self.get_user_from_db(self.match_info.prev_player)
                current_player_user = await self.get_user_from_db(self.match_info.current_player)
                await self.channel_layer.group_send(f'nations_notifications_{prev_player_user.pk}', {'type': 'new_turn'})
                await self.channel_layer.group_send(f'nations_notifications_{current_player_user.pk}', {'type': 'new_turn'})
                event_loop = asyncio.get_event_loop()
                event_loop.create_task(self.notify(self.match_info.current_player))

    async def received_chat(self, chat):
        match = await self.get_match_from_db()
        user = self.scope['user']
        if not user.is_authenticated:
            return
        chat_object = await self.save_chat_to_db(match, user, chat)
        group_message = {'type': 'chat_message', 'timestamp': chat_object.created.isoformat(), 'player': user.username, 'chat': chat}
        await self.channel_layer.group_send(self.match_group_name, group_message)

    async def received_notes(self, notes):
        await self.save_notes_to_db(notes)
        message = {
            'ack_notes': None
        }
        await self.send_json(message)

    async def receive_json(self, content):
        if not self.scope['user'].is_authenticated:
            await self.received_info_request()
            return
        if content is None:
            await self.received_info_request()
        elif 'join' in content:
            await self.received_join(content['join'])
        elif 'decline' in content:
            await self.received_decline()
        elif 'move' in content:
            await self.received_move(content['move'])
        elif 'chat' in content:
            await self.received_chat(content['chat'])
        elif 'notes' in content:
            await self.received_notes(content['notes'])
        elif 'keepalive' in content:
            await self.send_keepalive()

    async def state_change_message(self, event):
        if self.avoid_duplicate_updates:
            self.avoid_duplicate_updates = False
            return
        self.match_info.state = None
        if event['move'] is not None:
            await self.make_move(event['move'])
        await self.send_match_info()

    async def new_turn(self, event):
        await self.send_turns_info()

    async def send_match_info(self):
        await self.get_match_info()
        if self.match_info.players is None:
            return
        replay = self.match_info.replay
        players = self.match_info.players
        player_growth_resources = self.match_info.player_growth_resources
        if replay and players and player_growth_resources and all(player in player_growth_resources for player in players):
            accepted_players = players
        else:
            accepted_players = await self.get_accepted_players_from_db()
        message = {
            'players': players,
            'accepted': accepted_players,
            'growth_resources': player_growth_resources,
            'state': self.match_info.state,
            'log': self.match_info.log,
        }
        await self.send_json(message)
        self.sent_initial_info = True

    async def send_turns_info(self):
        number_of_turns = await self.get_number_of_turns_from_db()
        await self.send_json({'turns': number_of_turns})

    async def send_chat_log(self):
        chat_log = await self.get_chat_log_from_db()
        message = {
            'chat_log': chat_log
        }
        await self.send_json(message)

    async def send_keepalive(self):
        message = {
            'keepalive': None
        }
        await self.send_json(message)

    async def chat_message(self, event):
        message = {
            'chat': {
                'timestamp': event['timestamp'],
                'player': event['player'],
                'message': event['chat']
            }
        }
        await self.send_json(message)
        await self.get_chat_log_from_db()

    async def send_notes(self):
        notes = await self.get_notes_from_db()
        message = {
            'notes': notes
        }
        await self.send_json(message)

    @database_sync_to_async
    def notify(self, username):
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            return
        if user.turn_notification_emails:
            self.notify_email(user)

    def notify_email(self, user):
        hostname = Site.objects.get_current().domain
        match_url = reverse('Nations:match', kwargs={'pk': str(self.match_info.match_id)})
        subject = '[Tabony Games] Your turn!'
        body = f"""\
{user.username},

It's your turn in https://{hostname}{match_url}
"""
        if settings.USE_AMAZON_SES:
            games_app_config = apps.get_app_config('Games')
            try:
                response = games_app_config.aws_email_client.send_email(
                    Destination={
                        'ToAddresses': [
                            user.email,
                        ],
                    },
                    Message={
                        'Body': {
                            'Text': {
                                'Charset': 'UTF-8',
                                'Data': body,
                            },
                        },
                        'Subject': {
                            'Charset': 'UTF-8',
                            'Data': subject,
                        },
                    },
                    Source=settings.DEFAULT_FROM_EMAIL,
                )
            except Exception:
                pass
        else:
            send_mail(
                subject,
                body,
                None,
                [user.email],
                fail_silently=True
            )
