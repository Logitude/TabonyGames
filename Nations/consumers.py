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
import logging
import time

logger = logging.getLogger(__name__)

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
        self.match = None
        self.players = None
        self.match_players = None
        self.player_count = None
        self.growth_resources = None
        self.extra_draft_nations = None
        self.resource_remainder_tiebreaker = None
        self.card_draw_limits = None
        self.weighted_card_draw = None
        self.korea_nerf = None
        self.lincoln_nerf = None
        self.player_growth_resources = None
        self.replay_lines = []
        self.move_number = None
        self.prev_player = None
        self.current_player = None
        self.game_over = False
        self.log = None
        self.state = None

    def rules(self):
        rules = {'growth_resources': self.growth_resources}
        if self.extra_draft_nations != 0:
            rules['extra_draft_nations'] = self.extra_draft_nations
        for house_rule in ('resource_remainder_tiebreaker', 'card_draw_limits', 'weighted_card_draw', 'korea_nerf', 'lincoln_nerf'):
            if getattr(self, house_rule):
                rules[house_rule] = True
        if self.growth_resources < 0:
            rules['player_growth_resources'] = self.player_growth_resources
        return rules

class NationsMatchConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.timings = {}
        self.match_info = MatchInfo(self.scope['url_route']['kwargs']['match_id'])
        self.replay_info = MatchInfo(self.scope['url_route']['kwargs']['match_id'])
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
    def get_match_and_players_from_db(self):
        try:
            match = Match.objects.get(match_id=self.match_info.match_id)
        except Match.DoesNotExist:
            return (None, None, None)
        match_players = match.players.all().order_by('pk')
        players = [match_player.player for match_player in match_players][:match.player_count]
        return (match, players, match_players, match.current_player.username)

    async def get_match(self):
        (match, players, match_players, current_player) = await self.get_match_and_players_from_db()
        self.match_info.match = match
        self.match_info.players = players
        self.match_info.match_players = {match_player.player.username: match_player for match_player in match_players}
        self.match_info.player_count = match.player_count
        self.match_info.growth_resources = match.growth_resources
        self.match_info.extra_draft_nations = match.extra_draft_nations
        self.match_info.resource_remainder_tiebreaker = match.resource_remainder_tiebreaker
        self.match_info.card_draw_limits = match.card_draw_limits
        self.match_info.weighted_card_draw = match.weighted_card_draw
        self.match_info.korea_nerf = match.korea_nerf
        self.match_info.lincoln_nerf = match.lincoln_nerf
        self.match_info.replay_lines = match.replay.strip().splitlines()
        self.match_info.current_player = current_player
        self.match_info.game_over = match.game_over
        self.match_info.player_growth_resources = {player.username: await self.get_growth_resources(player) for player in players}

    @database_sync_to_async
    def save_match_to_db(self):
        self.timings['setting replay'] = time.monotonic_ns()
        match = self.match_info.match
        match.replay = '\n'.join(self.match_info.replay_lines).strip() + '\n'
        self.timings['set replay'] = time.monotonic_ns()
        if self.match_info.game_over:
            user = get_deleted_user()
        else:
            try:
                user = User.objects.get(username=self.match_info.current_player)
            except User.DoesNotExist:
                user = get_deleted_user()
        self.timings['got current player object'] = time.monotonic_ns()
        match.current_player = user
        if self.match_info.state is not None:
            match.current_player_order = ' '.join(self.match_info.state['player_order'])
            match.current_round = self.match_info.state['round']
        match.game_over = self.match_info.game_over
        if self.match_info.prev_player != self.match_info.current_player:
            match.new_turn = Now()
        self.timings['set other fields'] = time.monotonic_ns()
        match.save(update_fields=['replay', 'current_player', 'current_player_order', 'current_round', 'game_over', 'new_turn'])

    @database_sync_to_async
    def save_player_info_to_db(self, user):
        match_player = self.match_info.match_players[user.username]
        player_state = self.match_info.state['players'][user.username]
        nation = player_state['nation']
        if nation:
            match_player.nation = nation
            match_player.score = player_state['score']
            match_player.resource_remainder = player_state['resource_remainder']
            match_player.save(update_fields=['nation', 'score', 'resource_remainder'])

    async def save_match(self):
        self.timings['starting to save match'] = time.monotonic_ns()
        await self.save_match_to_db()
        self.timings['saved match to db'] = time.monotonic_ns()
        if self.match_info.state is not None:
            for player in self.match_info.players:
                await self.save_player_info_to_db(player)
        self.timings['saved player info to db'] = time.monotonic_ns()

    async def get_growth_resources(self, user):
        return self.match_info.match_players[user.username].growth_resources

    @database_sync_to_async
    def get_accepted_players_from_db(self):
        return [player.player.username for player in self.match_info.match.players.filter(accepted=True)]

    async def has_accepted(self):
        if not self.scope['user'].is_authenticated:
            return False
        return self.scope['user'].username in (await self.get_accepted_players_from_db())

    @database_sync_to_async
    def add_player_to_match_db(self, match, player, growth_resources):
        try:
            match_player = self.match_info.match_players[player.username]
        except KeyError:
            match_player = None
        if match_player is not None:
            match_player.growth_resources = growth_resources
            match_player.accepted = True
            match_player.save(update_fields=['growth_resources', 'accepted'])
        else:
            MatchPlayer.objects.create(match=match, player=player, growth_resources=growth_resources, accepted=True)
        match.new_turn = Now()
        match.save(update_fields=['new_turn'])

    @database_sync_to_async
    def remove_player_from_match_db(self, player):
        match_player = self.match_info.match_players[player.username]
        match_player.delete()
        match.new_turn = Now()
        match.save(update_fields['new_turn'])

    @database_sync_to_async
    def get_chat_log_from_db(self):
        match = self.match_info.match
        user = self.scope['user']
        if user.is_authenticated:
            try:
                match_player = self.match_info.match_players[user.username]
            except KeyError:
                match_player = None
        else:
            match_player = None
        chats = list(match.chats.all().order_by('pk'))
        if match_player is not None and chats:
            match_player.last_chat = chats[-1].pk
            match_player.save(update_fields=['last_chat'])
        chat_log = []
        for chat in chats:
            chat_log.append({'timestamp': chat.created.isoformat(), 'player': chat.player.username, 'message': chat.message})
        return chat_log

    @database_sync_to_async
    def save_chat_to_db(self, player, message):
        chat = NationsChat.objects.create(match=self.match_info.match, player=player, message=message)
        try:
            match_player = self.match_info.match_players[player.username]
        except KeyError:
            match_player = None
        if match_player is not None:
            match_player.last_chat = chat.pk
            match_player.save(update_fields=['last_chat'])
        return chat

    async def get_notes(self):
        user = self.scope['user']
        if user.is_authenticated:
            try:
                match_player = self.match_info.match_players[user.username]
            except KeyError:
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
                match_player = self.match_info.match_players[user.username]
            except KeyError:
                match_player = None
        else:
            match_player = None
        if match_player is not None:
            match_player.notes = notes[:4000]
            match_player.save(update_fields=['notes'])

    @database_sync_to_async
    def get_number_of_turns_from_db(self):
        user = self.scope['user']
        if not user.is_authenticated:
            return 0
        archive_threshold = make_aware(datetime.datetime.now() - datetime.timedelta(days=7))
        return len(Match.objects.filter(current_player=user, new_turn__gte=archive_threshold)) + len(MatchPlayer.objects.filter(player=user, accepted=False, match__new_turn__gte=archive_threshold))

    async def make_move(self, move):
        next_move = move
        if self.match_info.state is not None:
            undo_allowed = self.match_info.state['undo_allowed']
            if move is not None and move == 'UNDO':
                if undo_allowed:
                    self.match_info.replay_lines.pop()
                    next_move = None

        def move_getter(choice, options, undo_allowed):
            nonlocal next_move
            if next_move is None:
                raise TerminatePlay()
            option_strings = [str(option) for option in options]
            if next_move in option_strings:
                option = options[option_strings.index(next_move)]
            else:
                option = next_move
            next_move = None
            return option

        replay = '\n'.join(self.match_info.replay_lines).strip() + '\n'
        try:
            nations_match = nations.Match(move_getter=move_getter, replay=replay)
        except Exception:
            logger.warning('Failed to instantiate Nations match:')
            logger.warning(replay)
            logger.warning(move)
            logger.warning(self.match_info.match_id)
            logger.warning([player.username for player in self.match_info.players])
            import traceback
            traceback.print_exc()
        try:
            nations_match.play()
        except TerminatePlay:
            pass
        except Exception:
            import traceback
            traceback.print_exc()
        self.match_info.log = nations_match.get_log()
        self.match_info.state = nations_match.get_state()
        if move is not None and move != 'UNDO' and not self.match_info.state['invalid_move']:
            if '' not in self.match_info.replay_lines:
                self.match_info.replay_lines.append('')
            self.match_info.replay_lines.append(move)
        self.match_info.move_number = self.match_info.state['move_number']
        if self.replay_info.move_number is not None:
            if self.replay_info.move_number > self.match_info.move_number:
                self.replay_info.move_number = self.match_info.move_number
                (self.replay_info.log, self.replay_info.state) = await self.get_replay_log_state()
        self.match_info.prev_player = self.match_info.current_player
        self.match_info.current_player = self.match_info.state['next_move_player']
        self.match_info.game_over = self.match_info.state['game_over']

    async def get_replay_log_state(self):
        def move_getter(choice, options, undo_allowed):
            raise TerminatePlay()

        if '' not in self.match_info.replay_lines:
            index = len(self.match_info.replay_lines)
        else:
            index = self.match_info.replay_lines.index('') + 1 + self.replay_info.move_number
        replay = '\n'.join(self.match_info.replay_lines[:index]).strip() + '\n'
        nations_match = nations.Match(move_getter=move_getter, replay=replay)
        try:
            nations_match.play()
        except TerminatePlay:
            pass
        except Exception:
            import traceback
            traceback.print_exc()
        log = nations_match.get_log()
        state = nations_match.get_state()
        return (log, state)

    async def get_match_info(self):
        if self.match_info.replay_lines and self.match_info.state:
            return
        await self.get_match()
        if not self.match_info.replay_lines and len(await self.get_accepted_players_from_db()) == self.match_info.player_count:
            await self.create_match()
            await self.get_match()
        replay_lines = self.match_info.replay_lines
        state = self.match_info.state
        if replay_lines and not state:
            await self.make_move(None)
            self.match_info.move_number = self.match_info.state['move_number']
            self.match_info.current_player = self.match_info.state['next_move_player']
            self.match_info.game_over = self.match_info.state['game_over']

    async def create_match(self):
        def move_getter(choice, options, undo_allowed):
            raise TerminatePlay()

        rules = self.match_info.rules()
        player_names = [player.username for player in self.match_info.players]
        nations_match = nations.Match(player_names=player_names, move_getter=move_getter, rules=rules)
        try:
            nations_match.play()
        except TerminatePlay:
            pass
        self.match_info.replay_lines = nations_match.get_replay().strip().splitlines()
        self.match_info.log = nations_match.get_log()
        self.match_info.state = nations_match.get_state()
        self.match_info.move_number = self.match_info.state['move_number']
        self.match_info.current_player = self.match_info.state['next_move_player']
        self.match_info.game_over = self.match_info.state['game_over']
        await self.save_match()
        await self.notify()

    async def received_info_request(self):
        if not self.sent_initial_info:
            await self.send_chat_log()
            await self.send_notes()
            await self.send_match_info()
        await self.send_turns_info()

    async def received_join(self, join_info):
        user = self.scope['user']
        if not user.is_authenticated:
            return
        is_superuser = user.is_superuser
        players = self.match_info.players
        player_count = self.match_info.player_count
        if is_superuser or (user in players and await self.has_accepted()) or (user not in players and len(players) == player_count):
            return
        growth_resources = self.match_info.growth_resources if self.match_info.growth_resources > 0 else join_info
        await self.add_player_to_match_db(self.match_info.match, user, growth_resources)
        await self.get_match_info()
        await self.send_match_info()
        self.avoid_duplicate_updates = True
        group_message = {'type': 'state_change_message', 'move': None}
        await self.channel_layer.group_send(self.match_group_name, group_message)
        await self.channel_layer.group_send(f'nations_notifications_{user.pk}', {'type': 'new_turn'})

    async def received_decline(self):
        user = self.scope['user']
        if not user.is_authenticated:
            return
        await self.remove_player_from_match_db(user)
        await self.get_match_info()
        await self.send_match_info()
        self.avoid_duplicate_updates = True
        group_message = {'type': 'state_change_message', 'move': None}
        await self.channel_layer.group_send(self.match_group_name, group_message)
        await self.channel_layer.group_send(f'nations_notifications_{user.pk}', {'type': 'new_turn'})

    async def received_move(self, move):
        user = self.scope['user']
        if not user.is_authenticated:
            return
        self.timings['received move'] = time.monotonic_ns()
        username = user.username
        is_superuser = user.is_superuser
        if not self.match_info.game_over and (username == self.match_info.current_player or is_superuser):
            self.timings['checked user'] = time.monotonic_ns()
            await self.make_move(move)
            self.timings['made move'] = time.monotonic_ns()
            await self.save_match()
            self.timings['saved match'] = time.monotonic_ns()
            await self.send_match_info()
            self.timings['sent match info'] = time.monotonic_ns()
            self.avoid_duplicate_updates = True
            group_message = {'type': 'state_change_message', 'move': move}
            await self.channel_layer.group_send(self.match_group_name, group_message)
            self.timings['sent group message'] = time.monotonic_ns()
            if self.match_info.prev_player != self.match_info.current_player:
                await self.notify()
            self.timings['posted notify'] = time.monotonic_ns()

    async def received_chat(self, chat):
        user = self.scope['user']
        if not user.is_authenticated:
            return
        chat_object = await self.save_chat_to_db(user, chat)
        group_message = {'type': 'chat_message', 'timestamp': chat_object.created.isoformat(), 'player': user.username, 'chat': chat}
        await self.channel_layer.group_send(self.match_group_name, group_message)

    async def received_notes(self, notes):
        await self.save_notes_to_db(notes)
        message = {
            'ack_notes': None
        }
        await self.send_json(message)

    async def adjust_replay_position(self, position, command):
        for (command_type, command_value) in command.items():
            if command_type == 'round':
                if command_value in self.match_info.state['round_starts']:
                    return self.match_info.state['round_starts'][command_value]
                else:
                    return position
            elif command_type == 'back':
                if position > 0:
                    return position - 1
                else:
                    return 0
            elif command_type == 'stay':
                return position
            elif command_type == 'forward':
                if position < self.match_info.move_number:
                    return position + 1
                else:
                    return position
            elif command_type == 'to':
                if command_value <= 0:
                    return 0
                elif command_value > self.match_info.move_number:
                    return self.match_info.move_number
                else:
                    return command_value
            elif command_type == 'end':
                return None
        return position

    async def received_replay_command(self, command):
        if self.replay_info.move_number is None:
            self.replay_info.move_number = self.match_info.move_number
        self.replay_info.move_number = await self.adjust_replay_position(self.replay_info.move_number, command)
        if self.replay_info.move_number is None:
            await self.send_match_info()
            return
        (self.replay_info.log, self.replay_info.state) = await self.get_replay_log_state()
        await self.send_replay_info()

    async def receive_json(self, content):
        await self.get_match_info()
        self.timings = {}
        self.timings['received'] = time.monotonic_ns()
        await self.get_match_info()
        self.timings['got match info'] = time.monotonic_ns()
        if content is not None and 'replay' in content:
            await self.received_replay_command(content['replay'])
            return
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
        self.timings['sent'] = time.monotonic_ns()
        if self.timings['sent'] - self.timings['received'] > 300_000_000:
            logger.warning('')
            logger.warning(f'receive -> send took more than 0.3 seconds:')
            logger.warning(str(self.timings['received']))
            logger.warning(str(self.timings['sent']))
            logger.warning(str(content))
            logger.warning('')
            prev_time = self.timings['received']
            for (timestamp_name, timestamp_value) in self.timings.items():
                time_diff = timestamp_value - prev_time
                logger.warning(f'{timestamp_name:30s}: {time_diff:12d} ns ({time_diff // 1000000:6d} ms)')
                prev_time = timestamp_value
            logger.warning('')
        self.timings = {}

    async def state_change_message(self, event):
        if self.avoid_duplicate_updates:
            self.avoid_duplicate_updates = False
            return
        await self.get_match_info()
        if event['move'] is not None:
            await self.make_move(event['move'])
        await self.send_match_info()

    async def new_turn(self, event):
        await self.send_turns_info()

    async def send_match_info(self):
        if self.replay_info.move_number is not None:
            await self.send_replay_info()
            return
        await self.get_match_info()
        if self.match_info.players is None:
            return
        replay_lines = self.match_info.replay_lines
        players = self.match_info.players
        player_growth_resources = self.match_info.player_growth_resources
        if replay_lines and players and player_growth_resources and all(player.username in player_growth_resources for player in players):
            accepted_players = [player.username for player in players]
        else:
            accepted_players = await self.get_accepted_players_from_db()
        player_names = [player.username for player in self.match_info.players]
        message = {
            'replaying': False,
            'replay_position': self.match_info.move_number,
            'move_number': self.match_info.move_number,
            'players': player_names,
            'accepted': accepted_players,
            'growth_resources': player_growth_resources,
            'state': self.match_info.state,
            'log': self.match_info.log
        }
        await self.send_json(message)
        self.sent_initial_info = True

    async def send_replay_info(self):
        self.replay_info.state['round_starts'] = dict(self.match_info.state['round_starts'])
        player_names = [player.username for player in self.match_info.players]
        message = {
            'replaying': True,
            'replay_position': self.replay_info.move_number,
            'move_number': self.match_info.move_number,
            'players': player_names,
            'accepted': player_names,
            'growth_resources': self.match_info.player_growth_resources,
            'state': self.replay_info.state,
            'log': self.replay_info.log
        }
        await self.send_json(message)

    async def send_turns_info(self):
        number_of_turns = await self.get_number_of_turns_from_db()
        await self.send_json({'turns': number_of_turns})

    async def send_chat_log(self):
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

    async def send_notes(self):
        notes = await self.get_notes()
        message = {
            'notes': notes
        }
        await self.send_json(message)

    async def notify(self):
        if self.match_info.prev_player is not None:
            prev_player_user = await self.get_user_from_db(self.match_info.prev_player)
            await self.channel_layer.group_send(f'nations_notifications_{prev_player_user.pk}', {'type': 'new_turn'})
        if self.match_info.current_player is not None:
            current_player_user = await self.get_user_from_db(self.match_info.current_player)
            await self.channel_layer.group_send(f'nations_notifications_{current_player_user.pk}', {'type': 'new_turn'})
            if current_player_user.turn_notification_emails:
                event_loop = asyncio.get_event_loop()
                event_loop.create_task(self.notify_user(self.match_info.current_player, False))
        if self.match_info.game_over:
            for player in self.match_info.players:
                if player.turn_notification_emails:
                    event_loop = asyncio.get_event_loop()
                    event_loop.create_task(self.notify_user(player.username, True))

    async def notify_user(self, username, game_over):
        user = await self.get_user_from_db(username)
        try:
            await asyncio.wait_for(self.notify_email(user, game_over), timeout=1.0)
        except TimeoutError:
            return

    async def notify_email(self, user, game_over):
        hostname = (await sync_to_async(Site.objects.get_current)()).domain
        match_url = reverse('Nations:match', kwargs={'pk': str(self.match_info.match_id)})
        if game_over:
            subject = f'[Tabony Games] Nations match {self.match_info.match_id} complete!'
            body = f"""\
{user.username},

See the results at https://{hostname}{match_url}
"""
        else:
            subject = f'[Tabony Games] Your turn in Nations match {self.match_info.match_id}!'
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
