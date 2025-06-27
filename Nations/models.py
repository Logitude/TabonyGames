from django.db import models
from django.db.models.functions import Now

from users.models import User, get_deleted_user, get_deleted_user_id

class Match(models.Model):
    match_id = models.BigAutoField(primary_key=True)
    created = models.DateTimeField(auto_now_add=True, db_default=Now())
    new_turn = models.DateTimeField(db_default=Now())
    title = models.CharField(max_length=255, default='', blank=True)
    replay = models.TextField(default='', blank=True)
    player_count = models.IntegerField(default=4)
    growth_resources = models.IntegerField(default=2)
    extra_draft_nations = models.IntegerField(default=0)
    resource_remainder_tiebreaker = models.BooleanField(default=False)
    card_draw_limits = models.BooleanField(default=False)
    weighted_card_draw = models.BooleanField(default=False)
    korea_nerf = models.BooleanField(default=False)
    lincoln_nerf = models.BooleanField(default=False)
    current_player = models.ForeignKey(User, on_delete=models.SET(get_deleted_user), default=get_deleted_user_id)
    game_over = models.BooleanField(default=False)

    def __str__(self):
        return f'Nations match {self.match_id}'

class MatchPlayer(models.Model):
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='players')
    player = models.ForeignKey(User, on_delete=models.CASCADE)
    growth_resources = models.IntegerField(default=2)
    accepted = models.BooleanField(default=True)
    last_chat = models.IntegerField(default=0)
    notes = models.CharField(max_length=4000, default='', blank=True)

    def __str__(self):
        return f'{self.match}, player {self.player.username}'

class NationsChat(models.Model):
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='chats')
    player = models.ForeignKey(User, on_delete=models.CASCADE)
    created = models.DateTimeField(auto_now_add=True)
    message = models.CharField(max_length=255)

    def __str__(self):
        return f'{self.match}: {self.player.username}: {self.message}'

color_choices = ('Pink', 'Blue', 'Yellow', 'Orange', 'Green', 'Cyan', 'Red', 'Purple')

class NationsPreferences(models.Model):
    player = models.ForeignKey(User, on_delete=models.CASCADE)
    colors = models.CharField(max_length=255, default=', '.join(color_choices))
    symbols = models.BooleanField(default=False)

    def __str__(self):
        maybe_with_symbols = 'with symbols' if self.symbols else 'without symbols'
        return f'{self.player.username} likes {self.colors} {maybe_with_symbols}.'
