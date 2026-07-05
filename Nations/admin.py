from django.contrib import admin

from .models import Match, MatchPlayer, Tournament, NationsChat, NationsPreferences

admin.site.register(Match)
admin.site.register(MatchPlayer)
admin.site.register(Tournament)
admin.site.register(NationsChat)
admin.site.register(NationsPreferences)
