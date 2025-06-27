from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.urls import reverse
from django.contrib.sites.models import Site
from django.core.mail import send_mail
from django.db.models.functions import Now
from django.utils.timezone import make_aware

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

class NationsMatchConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.match_id = self.scope['url_route']['kwargs']['match_id']
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
        self.match_thread = None
        self.move_queue = None
        self.state_queue = None
        self.sent_current_info = False
        self.avoid_duplicate_updates = False
        self.sent_chat_log = False
        self.match_group_name = f'nations_match_{self.match_id}'
        await self.channel_layer.group_add(self.match_group_name, self.channel_name)
        user = self.scope['user']
        self.user_group_name = None
        if user.is_authenticated:
            user_id = user.pk
            self.user_group_name = f'nations_notifications_{user_id}'
            await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if self.match_thread is not None:
            self.move_queue.put(TerminatePlay)
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
            match = Match.objects.get(match_id=self.match_id)
        except Match.DoesNotExist:
            return None
        return match

    @database_sync_to_async
    def get_match_and_players_from_db(self):
        try:
            match = Match.objects.get(match_id=self.match_id)
        except Match.DoesNotExist:
            return (None, None, None)
        match_players = match.players.all().order_by('pk')
        players = [match_player.player.username for match_player in match_players][:match.player_count]
        return (match, players, match.current_player.username)

    async def get_match(self):
        (match, players, current_player) = await self.get_match_and_players_from_db()
        self.player_count = match.player_count
        self.growth_resources = match.growth_resources
        self.extra_draft_nations = match.extra_draft_nations
        self.resource_remainder_tiebreaker = match.resource_remainder_tiebreaker
        self.card_draw_limits = match.card_draw_limits
        self.weighted_card_draw = match.weighted_card_draw
        self.korea_nerf = match.korea_nerf
        self.lincoln_nerf = match.lincoln_nerf
        self.players = players
        self.replay = match.replay.replace('\r', '').rstrip('\n')
        self.current_player = current_player
        self.game_over = match.game_over

    @database_sync_to_async
    def save_match_to_db(self):
        try:
            match = Match.objects.get(match_id=self.match_id)
        except Match.DoesNotExist:
            return
        match.replay = self.replay + '\n'
        if self.game_over:
            user = get_deleted_user()
        else:
            try:
                user = User.objects.get(username=self.current_player)
            except User.DoesNotExist:
                user = get_deleted_user()
        match.current_player = user
        match.game_over = self.game_over
        if self.prev_player != self.current_player:
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
            match = Match.objects.get(match_id=self.match_id)
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
            match = Match.objects.get(match_id=self.match_id)
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
            match = Match.objects.get(match_id=self.match_id)
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
            self.state_queue.put((replay, log, state))

        def move_getter(choice, options, undo):
            while True:
                report_state(nations_match)
                next_move = self.move_queue.get()
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

        nations_match = nations.Match(move_getter=move_getter, replay=self.replay)
        try:
            nations_match.play()
        except TerminatePlay:
            return
        except Exception:
            import traceback
            traceback.print_exc()
        while True:
            report_state(nations_match)
            next_move = self.move_queue.get()
            if next_move is TerminatePlay:
                return

    async def get_match_info(self):
        if self.replay and self.state and self.match_thread and self.match_thread.is_alive():
            return
        await self.get_match()
        if not self.replay and len(await self.get_accepted_players_from_db()) == self.player_count:
            await self.create_match()
            await self.get_match()
        if (self.replay and not self.state) or (self.replay and self.state and (self.match_thread is None or not self.match_thread.is_alive())):
            if self.match_thread is None or not self.match_thread.is_alive():
                self.move_queue = queue.SimpleQueue()
                self.state_queue = queue.SimpleQueue()
                self.match_thread = threading.Thread(target=self.play_match)
                self.match_thread.start()
            else:
                self.move_queue.put(None)
            (self.replay, self.log, self.state) = self.state_queue.get()
            self.current_player = self.state['next_move_player']
            self.game_over = self.state['game_over']

    async def create_match(self):
        def move_getter(choice, options, undo):
            raise TerminatePlay()
        rules = {'growth_resources': self.growth_resources}
        if self.extra_draft_nations != 0:
            rules['extra_draft_nations'] = self.extra_draft_nations
        if self.resource_remainder_tiebreaker:
            rules['resource_remainder_tiebreaker'] = True
        if self.card_draw_limits:
            rules['card_draw_limits'] = True
        if self.weighted_card_draw:
            rules['weighted_card_draw'] = True
        if self.korea_nerf:
            rules['korea_nerf'] = True
        if self.lincoln_nerf:
            rules['lincoln_nerf'] = True
        if self.growth_resources < 0:
            rules['player_growth_resources'] = {player: await self.get_growth_resources_from_db(player) for player in self.players}
        nations_match = nations.Match(player_names=self.players, move_getter=move_getter, rules=rules)
        try:
            nations_match.play()
        except TerminatePlay:
            pass
        self.replay = nations_match.get_replay().replace('\r', '').rstrip('\n')
        self.log = nations_match.get_log().replace('\r', '').rstrip('\n')
        self.state = nations_match.get_state()
        self.current_player = self.state['next_move_player']
        self.game_over = self.state['game_over']
        await self.save_match()
        current_player_user = await self.get_user_from_db(self.current_player)
        await self.channel_layer.group_send(f'nations_notifications_{current_player_user.pk}', {'type': 'new_turn'})
        event_loop = asyncio.get_event_loop()
        event_loop.create_task(self.notify(self.current_player))

    async def make_move(self, move):
        if self.match_thread is None or not self.match_thread.is_alive():
            await self.get_match_info()
        self.move_queue.put(move)
        (self.replay, self.log, self.state) = self.state_queue.get()
        self.prev_player = self.current_player
        self.current_player = self.state['next_move_player']
        self.game_over = self.state['game_over']

    async def received_info_request(self):
        await self.send_chat_log()
        await self.get_match_info()
        if not self.sent_current_info:
            await self.send_match_info()
        await self.send_turns_info()

    async def received_join(self, join_info):
        await self.get_match_info()
        user = self.scope['user']
        if not user.is_authenticated:
            return
        username = user.username
        is_superuser = self.scope['user'].is_superuser
        if is_superuser or (username in self.players and await self.has_accepted()) or (username not in self.players and len(self.players) == self.player_count):
            return
        growth_resources = self.growth_resources if self.growth_resources > 0 else join_info
        match = await self.get_match_from_db()
        await self.add_player_to_match_db(match, user, growth_resources)
        await self.get_match_info()
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
        await self.get_match_info()
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
        if not self.game_over and (username == self.current_player or is_superuser):
            await self.make_move(move)
            await self.save_match()
            await self.send_match_info()
            self.avoid_duplicate_updates = True
            group_message = {'type': 'state_change_message', 'move': move}
            await self.channel_layer.group_send(self.match_group_name, group_message)
            if self.prev_player != self.current_player:
                prev_player_user = await self.get_user_from_db(self.prev_player)
                current_player_user = await self.get_user_from_db(self.current_player)
                await self.channel_layer.group_send(f'nations_notifications_{prev_player_user.pk}', {'type': 'new_turn'})
                await self.channel_layer.group_send(f'nations_notifications_{current_player_user.pk}', {'type': 'new_turn'})
                event_loop = asyncio.get_event_loop()
                event_loop.create_task(self.notify(self.current_player))

    async def received_chat(self, chat):
        match = await self.get_match_from_db()
        user = self.scope['user']
        if not user.is_authenticated:
            return
        chat_object = await self.save_chat_to_db(match, user, chat)
        group_message = {'type': 'chat_message', 'timestamp': chat_object.created.isoformat(), 'player': user.username, 'chat': chat}
        await self.channel_layer.group_send(self.match_group_name, group_message)

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

    async def state_change_message(self, event):
        if self.avoid_duplicate_updates:
            self.avoid_duplicate_updates = False
            return
        self.state = None
        if event['move'] is not None:
            await self.make_move(event['move'])
        await self.get_match_info()
        await self.send_match_info()

    async def new_turn(self, event):
        await self.send_turns_info()

    async def send_match_info(self):
        if self.players is None:
            return
        if self.replay and self.players and self.player_growth_resources and all(player in self.player_growth_resources for player in self.players):
            accepted_players = self.players
        else:
            accepted_players = await self.get_accepted_players_from_db()
            self.player_growth_resources = {player: await self.get_growth_resources_from_db(player) for player in self.players}
        message = {
            'players': self.players,
            'accepted': accepted_players,
            'growth_resources': self.player_growth_resources,
            'state': self.state,
            'log': self.log,
        }
        await self.send_json(message)
        self.sent_current_info = True

    async def send_turns_info(self):
        number_of_turns = await self.get_number_of_turns_from_db()
        await self.send_json({'turns': number_of_turns})

    async def send_chat_log(self):
        if self.sent_chat_log:
            return
        chat_log = await self.get_chat_log_from_db()
        message = {
            'chat_log': chat_log
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
        match_url = reverse('Nations:match', kwargs={'pk': str(self.match_id)})
        send_mail(
            '[Tabony Games] Your turn!',
            f"""\
{user.username},

It's your turn in https://{hostname}{match_url}
""",
            None,
            [user.email],
            fail_silently=True
        )
