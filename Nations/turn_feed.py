import logging

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.layers import get_channel_layer
from django.conf import settings
from django.db import transaction
from django.db.models import BooleanField, Case, Value, When

from users.models import User

from .models import Match, MatchPlayer

logger = logging.getLogger(__name__)
TURN_FEED_GROUP = 'nations_pending_turns'


def started_expression():
    return Case(When(replay='', then=Value(False)), default=Value(True), output_field=BooleanField())


def game_records(queryset):
    # Batch metadata only: no replay text, rules reconstruction, or per-game queries.
    rows = list(queryset.annotate(started=started_expression()).values(
        'match_id', 'title', 'player_count', 'started', 'current_player__username'))
    participants = {}
    for match_id, username, accepted in MatchPlayer.objects.filter(
            match_id__in=[row['match_id'] for row in rows]).order_by('pk').values_list(
                'match_id', 'player__username', 'accepted'):
        participants.setdefault(match_id, []).append((username, accepted))
    return [{
        'game': str(row['match_id']), 'title': row['title'],
        'players': [name for name, _ in participants.get(row['match_id'], [])],
        'invited': [name for name, accepted in participants.get(row['match_id'], []) if not accepted],
        'player_count': row['player_count'], 'started': row['started'],
        'player': row['current_player__username'] if row['started'] and row['current_player__username'] != 'deleted_player' else None,
    } for row in rows]


def publish_change(event, using=None):
    def publish():
        try:
            async_to_sync(get_channel_layer().group_send)(TURN_FEED_GROUP, event)
        except Exception:
            # An optional notifier must not fail a player's successfully saved move.
            logger.exception('Could not publish Nations feed change: %s', event)

    transaction.on_commit(publish, using=using)


def publish_turn_change(match_id, using=None):
    publish_change({'type': 'pending_turn_changed', 'game': str(match_id)}, using=using)


def publish_player_change(player_id, using=None):
    publish_change({'type': 'registered_player_changed', 'player_id': str(player_id)}, using=using)


class NationsTurnFeedConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.joined = False
        if not getattr(settings, 'NATIONS_TURN_FEED_ENABLED', False):
            await self.close(code=1008)
            return
        await self.channel_layer.group_add(TURN_FEED_GROUP, self.channel_name)
        self.joined = True
        await self.accept()
        await self.send_json(await self.get_snapshot())

    async def disconnect(self, close_code):
        if self.joined:
            await self.channel_layer.group_discard(TURN_FEED_GROUP, self.channel_name)

    @database_sync_to_async
    def get_snapshot(self):
        players = [{'id': str(pk), 'name': name} for pk, name in
                   User.objects.exclude(username='deleted_player').order_by('pk').values_list('pk', 'username')]
        games = game_records(Match.objects.filter(game_over=False).order_by('match_id'))
        return {'type': 'snapshot', 'players': players, 'games': games}

    @database_sync_to_async
    def get_game(self, game):
        records = game_records(Match.objects.filter(pk=game, game_over=False))
        return records[0] if records else None

    @database_sync_to_async
    def get_registered_player(self, player_id):
        return User.objects.filter(pk=player_id).exclude(username='deleted_player').values_list('username', flat=True).first()

    async def pending_turn_changed(self, event):
        # Resolve the latest committed game when handling an event. In particular,
        # a queued pre-snapshot event cannot restore an obsolete pending player.
        await self.send_json({'type': 'game', 'game': event['game'],
                              'details': await self.get_game(event['game'])})

    async def registered_player_changed(self, event):
        await self.send_json({'type': 'player', 'id': event['player_id'],
                              'name': await self.get_registered_player(event['player_id'])})

    async def receive_json(self, content):
        if content is not None:
            await self.close(code=1008)
            return
        # A lightweight heartbeat also renews Redis group membership; no DB scan.
        await self.channel_layer.group_add(TURN_FEED_GROUP, self.channel_name)
        await self.send_json({'type': 'pong'})
