from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import Client, TransactionTestCase
from django.urls import reverse

from Games.asgi import application
from users.models import User


class InvitationNotificationTests(TransactionTestCase):
    def test_confirming_a_match_updates_the_invitees_counters(self):
        organizer = User.objects.create_user(username='Organizer')
        invitees = [User.objects.create_user(username=name) for name in ('Alice', 'Bob')]
        self.client.force_login(organizer)

        invitee_clients = []
        for invitee in invitees:
            client = Client()
            client.force_login(invitee)
            invitee_clients.append(client)

        response = self.client.post(reverse('Nations:create'), {
            'title': 'Invitation notification',
            'player_count': '3',
            'growth_resources': '2',
            'extra_draft_nations': '0',
            'player1': organizer.username,
            'player2': invitees[0].username,
            'player3': invitees[1].username,
        })
        self.assertRedirects(response, reverse('Nations:confirm_create'), fetch_redirect_response=False)
        async_to_sync(self.check_counter_updates)(invitee_clients)

    async def check_counter_updates(self, invitee_clients):
        sockets = []
        try:
            for client in [self.client, *invitee_clients]:
                cookie = client.cookies[settings.SESSION_COOKIE_NAME].OutputString()
                socket = WebsocketCommunicator(
                    application, '/ws/nations/', headers=[(b'cookie', cookie.encode('ascii'))],
                )
                connected, _ = await socket.connect()
                self.assertTrue(connected)
                sockets.append(socket)
                await socket.send_json_to(None)
                self.assertEqual(await socket.receive_json_from(), {'turns': 0})

            response = await database_sync_to_async(self.client.post)(reverse('Nations:confirm_create'))
            self.assertEqual(response.status_code, 302)
            for socket in sockets[1:]:
                self.assertEqual(await socket.receive_json_from(), {'turns': 1})
            self.assertTrue(await sockets[0].receive_nothing())
        finally:
            for socket in sockets:
                if not socket.future.done():
                    await socket.disconnect()
