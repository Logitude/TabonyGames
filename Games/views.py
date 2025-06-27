from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib.auth import login, authenticate
from django.contrib.sites.models import Site
from django.core.mail import send_mail
from django.contrib.auth.models import Group
from django.http import HttpResponse, Http404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.contrib import messages
from django.utils.timezone import make_aware
from django.apps import apps

from users.models import User, is_superuser
from Nations.models import NationsPreferences, Match as NationsMatch, MatchPlayer as NationsMatchPlayer
from Nations.forms import NationsPreferencesForm
from Games.forms import UserCreationFormWithEmail, ProfileSettings

import datetime

def number_of_turns(user):
    if not user.is_authenticated:
        return {'Nations': 0}
    archive_threshold = make_aware(datetime.datetime.now() - datetime.timedelta(days=7))
    return {
        'Nations': len(NationsMatch.objects.filter(current_player=user, new_turn__gte=archive_threshold)) + len(NationsMatchPlayer.objects.filter(player=user, accepted=False, match__new_turn__gte=archive_threshold))
    }

def home(request):
    return render(request, 'home.html', {
        'IN_PRODUCTION': settings.IN_PRODUCTION,
        'turns': number_of_turns(request.user)
    })

def welcome(user):
    hostname = Site.objects.get_current().domain
    subject = 'Welcome to Tabony Games!'
    body = f"""\
Welcome, {user.username}!

You now have an account at https://{hostname} where you can play the board game(s) that Charles J. Tabony (Logitude) has implemented.

Enjoy!
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

def inform_me(user):
    subject = 'New registration at Tabony Games!'
    body = f"""\
{user.username} signed up!
"""
    if settings.USE_AMAZON_SES:
        games_app_config = apps.get_app_config('Games')
        try:
            response = games_app_config.aws_email_client.send_email(
                Destination={
                    'ToAddresses': [
                        settings.HELP_EMAIL,
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
            [settings.HELP_EMAIL],
            fail_silently=True
        )

def sign_up(request):
    if request.method == 'POST':
        form = UserCreationFormWithEmail(request.POST)
        if form.is_valid():
            form.save()
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password1')
            user = authenticate(username=username, password=password)
            login(request, user)
            welcome(user)
            inform_me(user)
            return redirect('home')
    else:
        form = UserCreationFormWithEmail()
    return render(request, 'registration/signup.html', {
        'IN_PRODUCTION': settings.IN_PRODUCTION,
        'form': form
    })

@login_required
def log_out(request):
    logout(request)
    return redirect('home')

@login_required
def profile(request):
    if request.method == 'POST':
        form = ProfileSettings(request.POST)
        nations_form = NationsPreferencesForm(request.POST)
        if form.is_valid() and nations_form.is_valid():
            user = request.user
            user.turn_notification_emails = form.cleaned_data.get('turn_notification_emails')
            user.save()
            nations_preferences = NationsPreferences.objects.get_or_create(player=request.user)[0]
            nations_preferences.colors = nations_form.cleaned_data.get('colors')
            nations_preferences.symbols = nations_form.cleaned_data.get('symbols')
            nations_preferences.save()
            messages.success(request, 'Successfully updated settings.')
            return redirect('profile')
    else:
        nations_preferences = NationsPreferences.objects.get_or_create(player=request.user)[0]
        form = ProfileSettings(instance=request.user)
        nations_form = NationsPreferencesForm(instance=nations_preferences)
    return render(request, 'registration/profile.html', {
        'IN_PRODUCTION': settings.IN_PRODUCTION,
        'help_email_address': settings.HELP_EMAIL,
        'form': form,
        'nations_form': nations_form
    })

if settings.IN_PRODUCTION:
    def protected_static(request, path):
        response = HttpResponse(status=200)
        del response['Content-Type']
        response['X-Accel-Redirect'] = '/static/protected_files/' + path
        return response
