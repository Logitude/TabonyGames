from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.urls import reverse
from django.contrib.sites.models import Site
from django.core.mail import send_mail
from django.db.models.functions import Now
from django.utils.timezone import make_aware

from users.models import User, get_deleted_user
from Nations.models import Match, MatchPlayer

import asyncio
import json
import datetime

class GamesConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope['user']
        self.user_group_name = None
        if not user.is_authenticated:
            return
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
            return {'Nations': 0}
        archive_threshold = make_aware(datetime.datetime.now() - datetime.timedelta(days=7))
        return {'Nations': len(Match.objects.filter(current_player=user, new_turn__gte=archive_threshold)) + len(MatchPlayer.objects.filter(player=user, accepted=False, match__new_turn__gte=archive_threshold))}

    async def new_turn(self, event):
        await self.send_turns_info()

    async def send_turns_info(self):
        number_of_turns = await self.get_number_of_turns_from_db()
        await self.send_json({'turns': number_of_turns})

    async def receive_json(self, content):
        await self.send_turns_info()
