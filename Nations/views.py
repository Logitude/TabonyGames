from django.conf import settings
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.decorators import login_required
from django.utils.timezone import make_aware
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponse

from users.models import User, get_deleted_user
from .models import Match, MatchPlayer, Tournament, NationsPreferences, NationsChat
from .forms import CreateMatchForm, CreateTournamentForm, ManageTournamentForm

from . import nations

import json
import datetime
import csv
import tarfile
import io

def number_of_turns(user):
    if not user.is_authenticated:
        return 0
    archive_threshold = make_aware(datetime.datetime.now() - datetime.timedelta(days=7))
    return len(Match.objects.filter(current_player=user, new_turn__gte=archive_threshold)) + len(MatchPlayer.objects.filter(player=user, accepted=False, match__new_turn__gte=archive_threshold))

def home(request):
    return render(request, 'Nations/home.html', {
        'IN_PRODUCTION': settings.IN_PRODUCTION,
        'turns': number_of_turns(request.user)
    })

@login_required
def create_match(request):
    if request.method == 'POST':
        form = CreateMatchForm(request.POST)
        if form.is_valid():
            request.session['match_options'] = json.dumps(form.cleaned_data)
            return redirect('Nations:confirm_create')
    else:
        form = CreateMatchForm()
    return render(request, 'Nations/create.html', {
        'IN_PRODUCTION': settings.IN_PRODUCTION,
        'turns': number_of_turns(request.user),
        'form': form
    })

@login_required
def confirm_create_match(request):
    if request.method == 'POST':
        match_options = request.session.pop('match_options', None)
    else:
        match_options = request.session.get('match_options', None)
    if match_options is None:
        return redirect('Nations:create')
    data = json.loads(match_options)
    if request.method == 'POST':
        player_names = [data['player1'], data['player2'], data['player3'], data['player4'], data['player5'], data['player6']]
        player_names = [player_name for player_name in player_names[:data['player_count']] if player_name]
        match = Match.objects.create(
            title=data['title'],
            player_count=data['player_count'],
            growth_resources=data['growth_resources'],
            extra_draft_nations=data['extra_draft_nations'],
            resource_remainder_tiebreaker=data['resource_remainder_tiebreaker'],
            weighted_card_draw=data['weighted_card_draw'],
            korea_nerf=data['korea_nerf'],
            lincoln_nerf=data['lincoln_nerf']
        )
        username = request.user.username
        for player_name in player_names:
            player = User.objects.get(username=player_name)
            accepted = player_name == username and data['growth_resources'] > 0
            match_player = MatchPlayer.objects.create(match=match, player=player, growth_resources=data['growth_resources'], accepted=accepted)
            match_player.save()
        match.save()
        return redirect('Nations:match', pk=match.match_id)
    return render(request, 'Nations/confirm_create.html', {
        'IN_PRODUCTION': settings.IN_PRODUCTION,
        'turns': number_of_turns(request.user),
        'data': data
    })

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
            house_rules.append('Card Draw')
        if match.korea_nerf:
            house_rules.append('Korea Nerf')
        if match.lincoln_nerf:
            house_rules.append('Lincoln Nerf')
        self.house_rules = ', '.join(house_rules)
        players = match.players.order_by('pk')
        self.players = [player.player.username for player in players]
        self.current_player = match.current_player.username if match.replay.strip() and not match.game_over else None
        self.invited = [player.player.username for player in players if not player.accepted]
        self.full = len(players) == match.player_count
        self.game_over = match.game_over
        self._new_turn = match.new_turn
        self.new_turn_iso = match.new_turn.isoformat()
        self.last_chat_seen = True
        if match_player is not None:
            try:
                last_chat = match.chats.latest('pk')
            except ObjectDoesNotExist:
                last_chat = None
            if last_chat is not None:
                last_chat_id = last_chat.pk
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
    return render(request, 'Nations/open_matches.html', {
        'IN_PRODUCTION': settings.IN_PRODUCTION,
        'turns': number_of_turns(request.user),
        'invited_matches': invited_matches,
        'open_matches': open_matches,
        'joined_matches': joined_matches,
        'full_matches': full_matches
    })

def matches(request):
    no_one = get_deleted_user()
    archive_threshold = make_aware(datetime.datetime.now() - datetime.timedelta(days=7))
    matches = [MatchProperties(match) for match in Match.objects.filter(game_over=False, new_turn__gte=archive_threshold).exclude(current_player=no_one).order_by('match_id')]
    ongoing_matches = matches
    ongoing_matches.sort(key=MatchProperties.new_turn.fget, reverse=True)
    return render(request, 'Nations/matches.html', {
        'IN_PRODUCTION': settings.IN_PRODUCTION,
        'turns': number_of_turns(request.user),
        'ongoing_matches': ongoing_matches
    })

def completed_matches(request):
    archive_threshold = make_aware(datetime.datetime.now() - datetime.timedelta(days=7))
    matches = [MatchProperties(match) for match in Match.objects.filter(game_over=True, new_turn__gte=archive_threshold).order_by('match_id')]
    matches.sort(key=MatchProperties.new_turn.fget, reverse=True)
    return render(request, 'Nations/completed_matches.html', {
        'IN_PRODUCTION': settings.IN_PRODUCTION,
        'turns': number_of_turns(request.user),
        'completed_matches': matches
    })

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
    return render(request, 'Nations/my_matches.html', {
        'IN_PRODUCTION': settings.IN_PRODUCTION,
        'turns': number_of_turns(request.user),
        'invited_matches': invited_matches,
        'my_turn_matches': my_turn_matches,
        'open_matches': open_matches,
        'other_matches': other_matches,
        'completed_matches': completed_matches
    })

def archive(request):
    return render(request, 'Nations/archive.html', {
        'IN_PRODUCTION': settings.IN_PRODUCTION,
        'turns': number_of_turns(request.user)
    })

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
    return render(request, 'Nations/archive_open.html', {
        'IN_PRODUCTION': settings.IN_PRODUCTION,
        'turns': number_of_turns(request.user),
        'invited_matches': invited_matches,
        'open_matches': open_matches,
        'joined_matches': joined_matches,
        'full_matches': full_matches
    })

def archive_ongoing(request):
    no_one = get_deleted_user()
    archive_threshold = make_aware(datetime.datetime.now() - datetime.timedelta(days=7))
    matches = [MatchProperties(match) for match in Match.objects.filter(game_over=False, new_turn__lt=archive_threshold).exclude(current_player=no_one).order_by('match_id')]
    ongoing_matches = matches
    ongoing_matches.sort(key=MatchProperties.new_turn.fget, reverse=True)
    return render(request, 'Nations/archive_ongoing.html', {
        'IN_PRODUCTION': settings.IN_PRODUCTION,
        'turns': number_of_turns(request.user),
        'ongoing_matches': ongoing_matches
    })

def archive_completed(request):
    archive_threshold = make_aware(datetime.datetime.now() - datetime.timedelta(days=7))
    matches = [MatchProperties(match) for match in Match.objects.filter(game_over=True, new_turn__lt=archive_threshold).order_by('-match_id')]
    return render(request, 'Nations/archive_completed.html', {
        'IN_PRODUCTION': settings.IN_PRODUCTION,
        'turns': number_of_turns(request.user),
        'completed_matches': matches
    })

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
    return render(request, 'Nations/archive_mine.html', {
        'IN_PRODUCTION': settings.IN_PRODUCTION,
        'turns': number_of_turns(request.user),
        'invited_matches': invited_matches,
        'my_turn_matches': my_turn_matches,
        'open_matches': open_matches,
        'other_matches': other_matches,
        'completed_matches': completed_matches
    })

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
    return render(request, 'Nations/match.html', {
        'IN_PRODUCTION': settings.IN_PRODUCTION,
        'turns': number_of_turns(request.user),
        'match': match_properties,
        'player_preferences': preferences,
        'abbrs': nations.abbr.abbrs
    })

def tournaments(request):
    tournaments = Tournament.objects.order_by('pk')
    return render(request, 'Nations/tournaments.html', {
        'IN_PRODUCTION': settings.IN_PRODUCTION,
        'turns': number_of_turns(request.user),
        'tournaments': tournaments
    })

def tournament(request, pk):
    tournament = get_object_or_404(Tournament, pk=pk)
    matches = [MatchProperties(match) for match in tournament.matches.order_by('match_id')]
    return render(request, 'Nations/tournament.html', {
        'IN_PRODUCTION': settings.IN_PRODUCTION,
        'turns': number_of_turns(request.user),
        'tournament': tournament,
        'matches': matches
    })

@login_required
def create_tournament(request):
    if request.method == 'POST':
        form = CreateTournamentForm(request.POST)
        if form.is_valid():
            tournament = Tournament.objects.create(
                title=form.cleaned_data.get('title'),
                organizer=request.user
            )
            return redirect('Nations:tournaments')
    else:
        form = CreateTournamentForm()
    return render(request, 'Nations/tournament_create.html', {
        'IN_PRODUCTION': settings.IN_PRODUCTION,
        'turns': number_of_turns(request.user),
        'form': form
    })

@login_required
def manage_tournament(request, pk):
    tournament = get_object_or_404(Tournament, pk=pk)
    authorized = request.user == tournament.organizer or request.user.is_superuser
    if request.method == 'POST' and authorized:
        form = ManageTournamentForm(request.POST, instance=tournament)
        if form.is_valid():
            tournament.title = form.cleaned_data.get('title')
            tournament.save()
            for match_id in form.cleaned_data.get('add_matches'):
                match = Match.objects.get(match_id=match_id)
                match.tournament = tournament
                match.save()
            for match_id in form.cleaned_data.get('remove_matches'):
                match = Match.objects.get(match_id=match_id)
                match.tournament = None
                match.save()
            return redirect('Nations:tournament', pk=pk)
    else:
        form = ManageTournamentForm(instance=tournament)
    return render(request, 'Nations/tournament_manage.html', {
        'IN_PRODUCTION': settings.IN_PRODUCTION,
        'turns': number_of_turns(request.user),
        'form': form,
        'tournament': tournament,
        'authorized': authorized
    })

def tournament_csv(request, pk):
    tournament = get_object_or_404(Tournament, pk=pk)
    response = HttpResponse(content_type='text/csv', headers={'Content-Disposition': f'attachment; filename=Tournament_{pk}_status.csv'})
    csv_writer = csv.writer(response)
    csv_writer.writerow((
        'Match ID',
        'Title',
        'Created',
        'Changed',
        'Player Count',
        'Growth Resources',
        'Extra Draft',
        'Resource Tiebreaker',
        'Card Draw',
        'Korea Nerf',
        'Lincoln Nerf',
        'Game Over',
        'Current Player Order',
        'Round',
        'P1 Name',
        'P1 Growth Resources',
        'P1 Nation',
        'P1 Score',
        'P1 Remainder',
        'P2 Name',
        'P2 Growth Resources',
        'P2 Nation',
        'P2 Score',
        'P2 Remainder',
        'P3 Name',
        'P3 Growth Resources',
        'P3 Nation',
        'P3 Score',
        'P3 Remainder',
        'P4 Name',
        'P4 Growth Resources',
        'P4 Nation',
        'P4 Score',
        'P4 Remainder',
        'P5 Name',
        'P5 Growth Resources',
        'P5 Nation',
        'P5 Score',
        'P5 Remainder',
        'P6 Name',
        'P6 Growth Resources',
        'P6 Nation',
        'P6 Score',
        'P6 Remainder'
    ))
    for match in tournament.matches.order_by('match_id'):
        row = [
            str(match.match_id),
            str(match.title),
            match.created.isoformat(timespec='seconds'),
            match.new_turn.isoformat(timespec='seconds'),
            str(match.player_count),
            str(match.growth_resources),
            str(match.extra_draft_nations),
            str(match.resource_remainder_tiebreaker).upper(),
            str(match.card_draw_limits or match.weighted_card_draw).upper(),
            str(match.korea_nerf).upper(),
            str(match.lincoln_nerf).upper(),
            str(match.game_over).upper(),
            str(match.current_player_order),
            str(match.current_round)
        ]
        for player in match.players.order_by('pk'):
            row += [
                str(player.player.username),
                str(player.growth_resources if match.growth_resources < 0 else match.growth_resources),
                str(player.nation),
                str(player.score),
                str(player.resource_remainder)
            ]
        csv_writer.writerow(row)
    return response

def stats(request):
    return render(request, 'Nations/stats.html', {
        'IN_PRODUCTION': settings.IN_PRODUCTION,
        'turns': number_of_turns(request.user)
    })

@login_required
def completed_matches_replays(request):
    matches = Match.objects.filter(game_over=True).order_by('match_id')
    filename = f'completed_matches_{len(matches)}.tar.gz'
    response = HttpResponse(content_type='application/gzip', headers={'Content-Disposition': f'attachment; filename={filename}'})
    tar = tarfile.open(filename, 'w|gz', fileobj=response)
    for match in matches:
        replay_buffer = io.BytesIO(match.replay.encode())
        tarinfo = tarfile.TarInfo(f'match{match.match_id:08d}.replay')
        tarinfo.size = replay_buffer.getbuffer().nbytes
        tar.addfile(tarinfo, fileobj=replay_buffer)
    return response
