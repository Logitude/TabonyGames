import datetime
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.db import transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from users.models import User
from .models import Match, MatchPlayer
from .turn_feed import NationsTurnFeedConsumer, TURN_FEED_GROUP, game_records


class TurnFeedTests(TransactionTestCase):
    def setUp(self):
        self.kayser = User.objects.create(username='Kayser')
        self.other = User.objects.create(username='OtherPlayer')
        self.match = Match.objects.create(current_player=self.kayser, replay='started')

    def socket(self):
        return WebsocketCommunicator(NationsTurnFeedConsumer.as_asgi(), '/ws/nations/turns/')

    def test_snapshot_all_ongoing_and_heartbeat_read_only(self):
        Match.objects.filter(pk=self.match.pk).update(new_turn=timezone.now() - datetime.timedelta(days=30))
        Match.objects.create(current_player=self.kayser, replay='')
        Match.objects.create(current_player=self.kayser, replay='ended', game_over=True)
        Match.objects.create(replay='started')  # deleted_player placeholder
        with self.assertNumQueries(1):
            self.assertEqual(Match.objects.filter(game_over=False).count(), 3)

        async def check():
            socket = self.socket()
            self.assertTrue((await socket.connect())[0])
            snapshot = await socket.receive_json_from()
            self.assertEqual(snapshot['type'], 'snapshot')
            self.assertEqual(len(snapshot['games']), 3)
            self.assertEqual(snapshot['games'][0]['game'], str(self.match.pk))
            self.assertEqual(snapshot['games'][0]['player'], 'Kayser')
            self.assertEqual(snapshot['players'], [{'id': str(self.kayser.pk), 'name': 'Kayser'},
                                                   {'id': str(self.other.pk), 'name': 'OtherPlayer'}])
            await socket.send_json_to(None)
            self.assertEqual(await socket.receive_json_from(), {'type': 'pong'})
            await socket.send_json_to({'move': 'PASS'})
            self.assertEqual((await socket.receive_output())['code'], 1008)
            await socket.disconnect()

        async_to_sync(check)()
        self.match.refresh_from_db()
        self.assertEqual(self.match.replay, 'started')

    def test_new_game_is_broadcast_to_each_feed_connection(self):
        async def check():
            sockets = [self.socket(), self.socket()]
            for socket in sockets:
                await socket.connect()
                await socket.receive_json_from()
            match = await database_sync_to_async(Match.objects.create)(current_player_id=self.other.pk, replay='started')
            for socket in sockets:
                event = await socket.receive_json_from()
                self.assertEqual(event['type'], 'game')
                self.assertEqual(event['game'], str(match.pk))
                self.assertEqual(event['details']['player'], 'OtherPlayer')
                await socket.disconnect()
        async_to_sync(check)()

    def test_changes_completion_deletion_and_no_same_player_updates(self):
        @database_sync_to_async
        def change(**fields):
            match = Match.objects.get(pk=self.match.pk)
            for key, value in fields.items():
                setattr(match, key, value)
            match.save()

        async def check():
            socket = self.socket()
            await socket.connect()
            await socket.receive_json_from()
            await change(replay='another choice by same player')
            self.assertTrue(await socket.receive_nothing(timeout=0.05))
            await change(current_player_id=self.other.pk)
            self.assertEqual((await socket.receive_json_from())['details']['player'], 'OtherPlayer')
            await change(game_over=True)
            self.assertEqual(await socket.receive_json_from(), {
                'type': 'game', 'game': str(self.match.pk), 'details': None})
            await database_sync_to_async(Match.objects.filter(pk=self.match.pk).delete)()
            self.assertEqual((await socket.receive_json_from())['details'], None)
            await socket.disconnect()
            replacement = self.socket()
            await replacement.connect()
            self.assertEqual((await replacement.receive_json_from())['games'], [])
            await replacement.disconnect()

        async_to_sync(check)()

    def test_queued_event_reads_latest_owner_instead_of_reverting_snapshot(self):
        async def check():
            socket = self.socket()
            await socket.connect()
            await socket.receive_json_from()
            await database_sync_to_async(Match.objects.filter(pk=self.match.pk).update)(current_player_id=self.other.pk)
            # Simulate an old queued marker: the consumer resolves current DB state.
            await get_channel_layer().group_send(TURN_FEED_GROUP, {
                'type': 'pending_turn_changed', 'game': str(self.match.pk)})
            self.assertEqual((await socket.receive_json_from())['details']['player'], 'OtherPlayer')
            await socket.disconnect()

        async_to_sync(check)()

    def test_publish_only_after_commit_and_never_on_rollback(self):
        with patch('Nations.turn_feed.async_to_sync') as sender:
            with transaction.atomic():
                self.match.current_player = self.other
                self.match.save()
                sender.assert_not_called()
            self.assertEqual(sender.call_count, 1)
            sender.reset_mock()
            with self.assertRaises(ValueError):
                with transaction.atomic():
                    self.match.current_player = self.kayser
                    self.match.save()
                    raise ValueError('rollback')
            sender.assert_not_called()

    def test_metadata_snapshot_waiting_invited_and_registered_names_without_games(self):
        MatchPlayer.objects.create(match=self.match, player=self.kayser)
        waiting = Match.objects.create(title='Waiting')
        MatchPlayer.objects.create(match=waiting, player=self.other, accepted=False)
        newcomer = User.objects.create(username='NoGamesYet', email='secret@example.com')
        consumer = NationsTurnFeedConsumer()
        with self.assertNumQueries(3):
            snapshot = NationsTurnFeedConsumer.get_snapshot.__wrapped__(consumer)
        self.assertIn({'id': str(newcomer.pk), 'name': 'NoGamesYet'}, snapshot['players'])
        self.assertNotIn('secret@example.com', str(snapshot))
        self.assertNotIn('password', str(snapshot))
        self.assertEqual(snapshot['games'][0]['players'], ['Kayser'])
        self.assertEqual(snapshot['games'][1], {
            'game': str(waiting.pk), 'title': 'Waiting', 'players': ['OtherPlayer'], 'invited': ['OtherPlayer'],
            'player_count': 4, 'started': False, 'player': None})

    def test_new_waiting_game_join_and_acceptance_update_metadata(self):
        async def check():
            socket = self.socket()
            await socket.connect()
            await socket.receive_json_from()
            match = await database_sync_to_async(Match.objects.create)(title='New game')
            event = await socket.receive_json_from()
            self.assertEqual(event['game'], str(match.pk))
            self.assertFalse(event['details']['started'])
            participant = await database_sync_to_async(MatchPlayer.objects.create)(match=match, player_id=self.other.pk, accepted=False)
            event = await socket.receive_json_from()
            self.assertEqual(event['details']['players'], ['OtherPlayer'])
            self.assertEqual(event['details']['invited'], ['OtherPlayer'])
            participant.accepted = True
            await database_sync_to_async(participant.save)()
            self.assertEqual((await socket.receive_json_from())['details']['invited'], [])
            participant.growth_resources = 3
            await database_sync_to_async(participant.save)()
            self.assertTrue(await socket.receive_nothing(timeout=0.05))
            await socket.disconnect()
        async_to_sync(check)()

    def test_registration_rename_deletion_and_no_login_events(self):
        MatchPlayer.objects.create(match=self.match, player=self.kayser)
        async def drain(socket):
            messages = []
            while not await socket.receive_nothing(timeout=0.05):
                messages.append(await socket.receive_json_from())
            return messages
        async def check():
            socket = self.socket()
            await socket.connect()
            await socket.receive_json_from()
            user = await database_sync_to_async(User.objects.create)(username='BrandNew')
            self.assertEqual(await socket.receive_json_from(), {'type': 'player', 'id': str(user.pk), 'name': 'BrandNew'})
            self.kayser.username = 'KAYSER'
            await database_sync_to_async(self.kayser.save)()
            events = await drain(socket)
            self.assertIn({'type': 'player', 'id': str(self.kayser.pk), 'name': 'KAYSER'}, events)
            game = next(e['details'] for e in events if e['type'] == 'game')
            self.assertEqual(game['players'], ['KAYSER'])
            self.assertEqual(game['player'], 'KAYSER')
            user.last_login = timezone.now()
            await database_sync_to_async(user.save)(update_fields=['last_login'])
            self.assertTrue(await socket.receive_nothing(timeout=0.05))
            deleted_id = str(self.kayser.pk)
            await database_sync_to_async(self.kayser.delete)()
            events = await drain(socket)
            self.assertIn({'type': 'player', 'id': deleted_id, 'name': None}, events)
            games = [e['details'] for e in events if e['type'] == 'game']
            self.assertTrue(games)
            for game in games:
                self.assertEqual(game['players'], [])
                self.assertIsNone(game['player'])
            replacement = self.socket()
            await replacement.connect()
            snapshot = await replacement.receive_json_from()
            self.assertNotIn('deleted_player', str(snapshot['players']))
            self.assertNotIn('KAYSER', str(snapshot))
            await replacement.disconnect()
            await socket.disconnect()
        async_to_sync(check)()

    def test_title_start_and_invitation_cleanup(self):
        invitation = MatchPlayer.objects.create(match=self.match, player=self.other, accepted=False)
        async def check():
            socket = self.socket()
            await socket.connect()
            await socket.receive_json_from()
            self.match.title = 'Renamed game'
            await database_sync_to_async(self.match.save)()
            self.assertEqual((await socket.receive_json_from())['details']['title'], 'Renamed game')
            self.match.replay = ''
            await database_sync_to_async(self.match.save)()
            self.assertFalse((await socket.receive_json_from())['details']['started'])
            self.match.replay = 'started again'
            await database_sync_to_async(self.match.save)()
            self.assertTrue((await socket.receive_json_from())['details']['started'])
            await database_sync_to_async(invitation.delete)()
            self.assertEqual((await socket.receive_json_from())['details']['players'], [])
            await socket.disconnect()
        async_to_sync(check)()

    def test_metadata_query_count_is_constant_across_many_games(self):
        Match.objects.bulk_create([Match(title=f'Game {i}', current_player=self.other) for i in range(100)])
        with self.assertNumQueries(2):
            self.assertEqual(len(game_records(Match.objects.filter(game_over=False))), 101)

    def test_score_only_save_adds_no_feed_query_or_event(self):
        participant = MatchPlayer.objects.create(match=self.match, player=self.kayser)
        participant.score = 42
        with patch('Nations.turn_feed.async_to_sync') as sender:
            with self.assertNumQueries(1):
                participant.save(update_fields=['score', 'nation', 'resource_remainder'])
            sender.assert_not_called()

    def test_broadcast_failure_does_not_break_saved_move(self):
        with patch('Nations.turn_feed.get_channel_layer', side_effect=RuntimeError('offline')):
            with self.assertLogs('Nations.turn_feed', level='ERROR'):
                self.match.current_player = self.other
                self.match.save()
        self.match.refresh_from_db()
        self.assertEqual(self.match.current_player_id, self.other.pk)

    @override_settings(NATIONS_TURN_FEED_ENABLED=False)
    def test_disabled_rejects_connections_and_adds_no_save_queries(self):
        async def check():
            socket = self.socket()
            self.assertFalse((await socket.connect())[0])
            await socket.disconnect()
        async_to_sync(check)()
        with self.assertNumQueries(1):
            self.match.current_player = self.other
            self.match.save()
