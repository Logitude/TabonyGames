from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.decorators import login_required
from django.utils.timezone import make_aware

from users.models import User, get_deleted_user
from .models import Match, MatchPlayer, NationsPreferences, NationsChat
from .forms import CreateMatchForm

from . import nations

import json
import datetime

def number_of_turns(user):
    if not user.is_authenticated:
        return 0
    archive_threshold = make_aware(datetime.datetime.now() - datetime.timedelta(days=7))
    return len(Match.objects.filter(current_player=user, new_turn__gte=archive_threshold)) + len(MatchPlayer.objects.filter(player=user, accepted=False, match__new_turn__gte=archive_threshold))

def home(request):
    return render(request, 'Nations/home.html', {'turns': number_of_turns(request.user)})

@login_required
def create_match(request):
    if request.method == 'POST':
        form = CreateMatchForm(request.POST)
        if form.is_valid():
            form_data = form.cleaned_data
            title = form_data.get('title')
            player_count = form_data.get('player_count')
            growth_resources = form_data.get('growth_resources')
            player_names = [form_data.get('player1'), form_data.get('player2'), form_data.get('player3'), form_data.get('player4'), form_data.get('player5'), form_data.get('player6')]
            player_names = [player_name for player_name in player_names[:player_count] if player_name]
            match = Match.objects.create(title=title, player_count=player_count, growth_resources=growth_resources)
            match.extra_draft_nations = form_data.get('extra_draft_nations')
            if form_data.get('resource_remainder_tiebreaker'):
                match.resource_remainder_tiebreaker = True
            if form_data.get('card_draw_limits'):
                match.card_draw_limits = True
            if form_data.get('weighted_card_draw'):
                match.weighted_card_draw = True
            if form_data.get('korea_nerf'):
                match.korea_nerf = True
            if form_data.get('lincoln_nerf'):
                match.lincoln_nerf = True
            username = request.user.username
            for player_name in player_names:
                player = User.objects.get(username=player_name)
                accepted = player_name == username and growth_resources > 0
                match_player = MatchPlayer.objects.create(match=match, player=player, growth_resources=growth_resources, accepted=accepted)
                match_player.save()
            match.save()
            return redirect('Nations:match', pk=match.match_id)
    else:
        form = CreateMatchForm()
    return render(request, 'Nations/create.html', {'turns': number_of_turns(request.user), 'form': form})

class MatchProperties:
    growth_resources_descriptions = {
        1: 'Emperor',
        2: 'King',
        3: 'Prince',
        4: 'Chieftain',
        -1: 'Variable Growth Resources'
    }

    def __init__(self, match, match_player=None):
        self._match_id = match.match_id
        self.title = match.title
        self.player_count = match.player_count
        self.growth_resources = match.growth_resources
        self.match_type = f'{self.player_count}-Player, {self.growth_resources_descriptions[self.growth_resources]}'
        house_rules = []
        if match.extra_draft_nations != 0:
            if match.extra_draft_nations < 0:
                house_rules.append('Draft-from-All')
            else:
                house_rules.append(f'+{match.extra_draft_nations} Draft')
        if match.resource_remainder_tiebreaker:
            house_rules.append('Tiebreaker')
        if match.card_draw_limits:
            house_rules.append('Card Draw Limits')
        if match.weighted_card_draw:
            house_rules.append('Weighted Card Draw')
        if match.korea_nerf:
            house_rules.append('Korea Nerf')
        if match.lincoln_nerf:
            house_rules.append('Lincoln Nerf')
        self.house_rules = ', '.join(house_rules)
        players = match.players.order_by('pk')
        self.players = [player.player.username for player in players]
        self.current_player = match.current_player.username if match.replay else None
        self.invited = [player.player.username for player in players if not player.accepted]
        self.full = len(players) == match.player_count
        self.game_over = match.game_over
        self._new_turn = match.new_turn
        self.new_turn_iso = match.new_turn.isoformat()
        self.last_chat_seen = True
        if match_player is not None:
            match_chats = list(match.chats.all().order_by('pk'))
            if match_chats:
                last_chat_id = match_chats[-1].pk
                self.last_chat_seen = last_chat_id <= match_player.last_chat

    def get_match_id(self):
        return self._match_id

    def get_new_turn(self):
        return self._new_turn

    match_id = property(get_match_id)
    new_turn = property(get_new_turn)

def open_matches(request):
    user = request.user
    if user.is_authenticated:
        username = request.user.username
    else:
        username = ''
    no_one = get_deleted_user()
    archive_threshold = make_aware(datetime.datetime.now() - datetime.timedelta(days=7))
    matches = [MatchProperties(match) for match in Match.objects.filter(game_over=False, current_player=no_one, new_turn__gte=archive_threshold).order_by('match_id')]
    invited_matches = []
    open_matches = []
    joined_matches = []
    full_matches = []
    for match in matches:
        if match.current_player:
            pass
        elif username in match.invited:
            invited_matches.append(match)
        elif username in match.players:
            joined_matches.append(match)
        elif match.full:
            full_matches.append(match)
        else:
            open_matches.append(match)
    info = {
        'turns': number_of_turns(request.user),
        'invited_matches': invited_matches,
        'open_matches': open_matches,
        'joined_matches': joined_matches,
        'full_matches': full_matches
    }
    return render(request, 'Nations/open_matches.html', info)

def matches(request):
    no_one = get_deleted_user()
    archive_threshold = make_aware(datetime.datetime.now() - datetime.timedelta(days=7))
    matches = [MatchProperties(match) for match in Match.objects.filter(game_over=False, new_turn__gte=archive_threshold).exclude(current_player=no_one).order_by('match_id')]
    ongoing_matches = matches
    ongoing_matches.sort(key=MatchProperties.new_turn.fget, reverse=True)
    info = {
        'turns': number_of_turns(request.user),
        'ongoing_matches': ongoing_matches
    }
    return render(request, 'Nations/matches.html', info)

def completed_matches(request):
    archive_threshold = make_aware(datetime.datetime.now() - datetime.timedelta(days=7))
    matches = [MatchProperties(match) for match in Match.objects.filter(game_over=True, new_turn__gte=archive_threshold).order_by('match_id')]
    matches.sort(key=MatchProperties.new_turn.fget, reverse=True)
    info = {
        'turns': number_of_turns(request.user),
        'completed_matches': matches
    }
    return render(request, 'Nations/completed_matches.html', info)

@login_required
def my_matches(request):
    username = request.user.username
    archive_threshold = make_aware(datetime.datetime.now() - datetime.timedelta(days=7))
    matches = [MatchProperties(match_player.match, match_player) for match_player in MatchPlayer.objects.filter(player=request.user, match__new_turn__gte=archive_threshold)]
    invited_matches = []
    my_turn_matches = []
    open_matches = []
    other_matches = []
    completed_matches = []
    for match in matches:
        if match.game_over:
            completed_matches.append(match)
        elif username in match.invited:
            invited_matches.append(match)
        elif match.current_player == username:
            my_turn_matches.append(match)
        elif match.current_player:
            other_matches.append(match)
        else:
            open_matches.append(match)
    invited_matches.sort(key=MatchProperties.new_turn.fget, reverse=True)
    my_turn_matches.sort(key=MatchProperties.new_turn.fget, reverse=True)
    open_matches.sort(key=MatchProperties.new_turn.fget, reverse=True)
    other_matches.sort(key=MatchProperties.new_turn.fget, reverse=True)
    completed_matches.sort(key=MatchProperties.new_turn.fget, reverse=True)
    info = {
        'turns': number_of_turns(request.user),
        'invited_matches': invited_matches,
        'my_turn_matches': my_turn_matches,
        'open_matches': open_matches,
        'other_matches': other_matches,
        'completed_matches': completed_matches
    }
    return render(request, 'Nations/my_matches.html', info)

def archive(request):
    info = {
        'turns': number_of_turns(request.user)
    }
    return render(request, 'Nations/archive.html', info)

def archive_open(request):
    user = request.user
    if user.is_authenticated:
        username = request.user.username
    else:
        username = ''
    no_one = get_deleted_user()
    archive_threshold = make_aware(datetime.datetime.now() - datetime.timedelta(days=7))
    matches = [MatchProperties(match) for match in Match.objects.filter(game_over=False, current_player=no_one, new_turn__lt=archive_threshold).order_by('-match_id')]
    invited_matches = []
    open_matches = []
    joined_matches = []
    full_matches = []
    for match in matches:
        if match.current_player:
            pass
        elif username in match.invited:
            invited_matches.append(match)
        elif username in match.players:
            joined_matches.append(match)
        elif match.full:
            full_matches.append(match)
        else:
            open_matches.append(match)
    info = {
        'turns': number_of_turns(request.user),
        'invited_matches': invited_matches,
        'open_matches': open_matches,
        'joined_matches': joined_matches,
        'full_matches': full_matches
    }
    return render(request, 'Nations/archive_open.html', info)

def archive_ongoing(request):
    no_one = get_deleted_user()
    archive_threshold = make_aware(datetime.datetime.now() - datetime.timedelta(days=7))
    matches = [MatchProperties(match) for match in Match.objects.filter(game_over=False, new_turn__lt=archive_threshold).exclude(current_player=no_one).order_by('match_id')]
    ongoing_matches = matches
    ongoing_matches.sort(key=MatchProperties.new_turn.fget, reverse=True)
    info = {
        'turns': number_of_turns(request.user),
        'ongoing_matches': ongoing_matches
    }
    return render(request, 'Nations/archive_ongoing.html', info)

def archive_completed(request):
    archive_threshold = make_aware(datetime.datetime.now() - datetime.timedelta(days=7))
    matches = [MatchProperties(match) for match in Match.objects.filter(game_over=True, new_turn__lt=archive_threshold).order_by('-match_id')]
    info = {
        'turns': number_of_turns(request.user),
        'completed_matches': matches
    }
    return render(request, 'Nations/archive_completed.html', info)

@login_required
def archive_mine(request):
    username = request.user.username
    archive_threshold = make_aware(datetime.datetime.now() - datetime.timedelta(days=7))
    matches = [MatchProperties(match_player.match, match_player) for match_player in MatchPlayer.objects.filter(player=request.user, match__new_turn__lt=archive_threshold)]
    invited_matches = []
    my_turn_matches = []
    open_matches = []
    other_matches = []
    completed_matches = []
    for match in matches:
        if match.game_over:
            completed_matches.append(match)
        elif username in match.invited:
            invited_matches.append(match)
        elif match.current_player == username:
            my_turn_matches.append(match)
        elif match.current_player:
            other_matches.append(match)
        else:
            open_matches.append(match)
    invited_matches.sort(key=MatchProperties.new_turn.fget, reverse=True)
    my_turn_matches.sort(key=MatchProperties.new_turn.fget, reverse=True)
    open_matches.sort(key=MatchProperties.new_turn.fget, reverse=True)
    other_matches.sort(key=MatchProperties.new_turn.fget, reverse=True)
    completed_matches.sort(key=MatchProperties.new_turn.fget, reverse=True)
    info = {
        'turns': number_of_turns(request.user),
        'invited_matches': invited_matches,
        'my_turn_matches': my_turn_matches,
        'open_matches': open_matches,
        'other_matches': other_matches,
        'completed_matches': completed_matches
    }
    return render(request, 'Nations/archive_mine.html', info)

def match(request, pk):
    match = get_object_or_404(Match, match_id=pk)
    match_properties = MatchProperties(match)
    user = request.user
    if user.is_authenticated:
        preferences = NationsPreferences.objects.get_or_create(player=request.user)[0]
        preferences = {'colors': preferences.colors, 'symbols': preferences.symbols}
    else:
        player_colors = NationsPreferences._meta.get_field('colors').get_default()
        player_symbols = NationsPreferences._meta.get_field('symbols').get_default()
        preferences = {'colors': player_colors, 'symbols': player_symbols}
    info = {
        'turns': number_of_turns(request.user),
        'match': match_properties,
        'player_preferences': preferences,
        'abbrs': nations.abbr.abbrs
    }
    return render(request, 'Nations/match.html', info)
