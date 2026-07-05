from django.core.management.base import BaseCommand

from Nations.models import Match, MatchPlayer

from ... import nations

class Command(BaseCommand):
    help = 'Update the database to match the current match state.'

    def handle(self, *args, **kwargs):
        class TerminatePlay(Exception):
            pass

        def move_getter(choice, options, undo):
            raise TerminatePlay()

        matches = Match.objects.order_by('pk')
        for match in matches:
            if not match.replay:
                continue
            nations_match = nations.Match(move_getter=move_getter, replay=match.replay.replace('\r', '').rstrip('\n'))
            try:
                nations_match.play()
            except TerminatePlay:
                pass
            except Exception:
                import traceback
                traceback.print_exc()
            state = nations_match.get_state()
            match.current_player_order = ' '.join(state['player_order'])
            match.current_round = state['round']
            match.save()
            for player in match.players.all().order_by('pk'):
                player_state = state['players'][player.player.username]
                nation = player_state['nation']
                if nation:
                    player.nation = nation
                    player.score = player_state['score']
                    player.resource_remainder = player_state['resource_remainder']
                    player.save()
