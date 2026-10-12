from django.conf import settings
from django.db.models.signals import pre_save, post_save, pre_delete, post_delete
from django.dispatch import receiver
from django.db.models import Q

from users.models import User

from .models import Match, MatchPlayer
from .turn_feed import publish_turn_change, publish_player_change, started_expression


def enabled(raw=False):
    return not raw and getattr(settings, 'NATIONS_TURN_FEED_ENABLED', False)


PARTICIPANT_FIELDS = {'match', 'match_id', 'player', 'player_id', 'accepted'}


@receiver(pre_save, sender=Match)
def remember_turn_owner(sender, instance, raw=False, using=None, **kwargs):
    if not enabled(raw):
        return
    instance._previous_feed_turn = sender.objects.using(using).filter(
        pk=instance.pk).annotate(started=started_expression()).values_list(
            'current_player_id', 'game_over', 'title', 'player_count', 'started').first()


@receiver(post_save, sender=Match)
def broadcast_turn_owner(sender, instance, raw=False, using=None, **kwargs):
    if not enabled(raw):
        return
    before = getattr(instance, '_previous_feed_turn', None)
    after = (instance.current_player_id, instance.game_over, instance.title, instance.player_count, bool(instance.replay))
    if before != after:
        publish_turn_change(instance.pk, using=using)


@receiver(post_delete, sender=Match)
def broadcast_removed_match(sender, instance, using=None, **kwargs):
    if enabled():
        publish_turn_change(instance.pk, using=using)


@receiver(pre_save, sender=MatchPlayer)
def remember_participant(sender, instance, raw=False, using=None, update_fields=None, **kwargs):
    if enabled(raw) and (update_fields is None or PARTICIPANT_FIELDS & set(update_fields)):
        instance._previous_feed_participant = sender.objects.using(using).filter(pk=instance.pk).values_list(
            'match_id', 'player_id', 'accepted').first()


@receiver(post_save, sender=MatchPlayer)
def broadcast_participant(sender, instance, raw=False, using=None, update_fields=None, **kwargs):
    if not enabled(raw) or (update_fields is not None and not PARTICIPANT_FIELDS & set(update_fields)):
        return
    before = getattr(instance, '_previous_feed_participant', None)
    if before != (instance.match_id, instance.player_id, instance.accepted):
        publish_turn_change(instance.match_id, using=using)
        if before and before[0] != instance.match_id:
            publish_turn_change(before[0], using=using)


@receiver(post_delete, sender=MatchPlayer)
def broadcast_removed_participant(sender, instance, using=None, **kwargs):
    # Covers declined invitations and administrative cleanup, not a leave operation.
    if enabled():
        publish_turn_change(instance.match_id, using=using)


def related_games(player_id, using):
    return list(Match.objects.using(using).filter(game_over=False).filter(
        Q(current_player_id=player_id) | Q(players__player_id=player_id)).values_list('pk', flat=True).distinct())


@receiver(pre_save, sender=User)
def remember_username(sender, instance, raw=False, using=None, update_fields=None, **kwargs):
    if enabled(raw) and (update_fields is None or 'username' in update_fields):
        instance._previous_feed_name = sender.objects.using(using).filter(pk=instance.pk).values_list('username', flat=True).first()


@receiver(post_save, sender=User)
def broadcast_username(sender, instance, raw=False, using=None, update_fields=None, created=False, **kwargs):
    if not enabled(raw) or (update_fields is not None and 'username' not in update_fields):
        return
    if created:
        if instance.username != 'deleted_player':
            publish_player_change(instance.pk, using=using)
        return
    if getattr(instance, '_previous_feed_name', None) != instance.username:
        publish_player_change(instance.pk, using=using)
        for game in related_games(instance.pk, using):
            publish_turn_change(game, using=using)


@receiver(pre_delete, sender=User)
def remember_deleted_player_games(sender, instance, using=None, **kwargs):
    if enabled():
        instance._feed_games = related_games(instance.pk, using)


@receiver(post_delete, sender=User)
def broadcast_deleted_player(sender, instance, using=None, **kwargs):
    if enabled():
        publish_player_change(instance.pk, using=using)
        for game in getattr(instance, '_feed_games', []):
            publish_turn_change(game, using=using)
