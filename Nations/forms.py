from django import forms
from users.models import User
from .models import NationsPreferences, color_choices

class CreateMatchForm(forms.Form):
    title = forms.CharField(label='Title', required=False, max_length=255, widget=forms.TextInput(attrs={'onkeydown': 'return event.key != \'Enter\';'}))
    player_count = forms.ChoiceField(
        label='Number of Players',
        choices=(
            ('2', '2'),
            ('3', '3'),
            ('4', '4'),
            ('5', '5'),
            ('6', '6')
        ),
        initial='4',
        widget=forms.Select(attrs={'class': 'form-select', 'onkeydown': 'return event.key != \'Enter\';'})
    )
    growth_resources = forms.ChoiceField(
        label='Growth Resources',
        choices=(
            ('1', '1 - Emperor'),
            ('2', '2 - King'),
            ('3', '3 - Prince'),
            ('4', '4 - Chieftain'),
            ('-1', 'Player Choice')
        ),
        initial='2',
        widget=forms.Select(attrs={'class': 'form-select', 'onkeydown': 'return event.key != \'Enter\';'})
    )
    player1 = forms.CharField(label='Player1', required=False, widget=forms.TextInput(attrs={'placeholder': 'Open', 'onkeydown': 'return event.key != \'Enter\';'}))
    player2 = forms.CharField(label='Player2', required=False, widget=forms.TextInput(attrs={'placeholder': 'Open', 'onkeydown': 'return event.key != \'Enter\';'}))
    player3 = forms.CharField(label='Player3', required=False, widget=forms.TextInput(attrs={'placeholder': 'Open', 'onkeydown': 'return event.key != \'Enter\';'}))
    player4 = forms.CharField(label='Player4', required=False, widget=forms.TextInput(attrs={'placeholder': 'Open', 'onkeydown': 'return event.key != \'Enter\';'}))
    player5 = forms.CharField(label='Player5', required=False, widget=forms.TextInput(attrs={'placeholder': 'Open', 'onkeydown': 'return event.key != \'Enter\';'}))
    player6 = forms.CharField(label='Player6', required=False, widget=forms.TextInput(attrs={'placeholder': 'Open', 'onkeydown': 'return event.key != \'Enter\';'}))
    extra_draft_nations = forms.ChoiceField(
        label='Extra Nations to Draft From',
        choices=(
            ('0', '+0 (One Nation Per Player Total)'),
            ('1', '+1'),
            ('2', '+2'),
            ('3', '+3'),
            ('4', '+4'),
            ('5', '+5'),
            ('6', '+6'),
            ('-1', 'All Nations Available')
        ),
        initial='1',
        widget=forms.Select(attrs={'class': 'form-select', 'onkeydown': 'return event.key != \'Enter\';'})
    )
    resource_remainder_tiebreaker = forms.BooleanField(
        required=False,
        label='Resource Remainder as Tiebreaker',
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'onkeydown': 'return event.key != \'Enter\';'})
    )
    weighted_card_draw = forms.BooleanField(
        required=False,
        label='Weighted Card Draw',
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'onkeydown': 'return event.key != \'Enter\';'})
    )
    korea_nerf = forms.BooleanField(
        required=False,
        label='Korea Nerf',
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'onkeydown': 'return event.key != \'Enter\';'})
    )
    lincoln_nerf = forms.BooleanField(
        required=False,label='Lincoln Nerf',
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'onkeydown': 'return event.key != \'Enter\';'})
    )

    def clean(self):
        player_names = []
        for player in ('player1', 'player2', 'player3', 'player4', 'player5', 'player6'):
            player_name = self.cleaned_data[player]
            if player_name and not User.objects.filter(username=player_name).exists():
                self.add_error(player, 'No player with that username.')
            if player_name:
                if player_name in player_names:
                    self.add_error(player, 'Duplicate username.')
                player_names.append(player_name)
        for field in ('player_count', 'growth_resources', 'extra_draft_nations'):
            try:
                self.cleaned_data[field] = int(self.cleaned_data[field])
            except (ValueError, TypeError):
                self.add_error(field, 'Invalid choice.')
        return self.cleaned_data

class NationsPreferencesForm(forms.ModelForm):
    class Meta:
        model = NationsPreferences
        exclude = ('player', 'colors', 'symbols')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        nations_preferences = kwargs.get('instance', None)
        if nations_preferences is not None:
            selected_colors = nations_preferences.colors.split(', ')
            symbols = nations_preferences.symbols
        else:
            selected_colors = color_choices
            symbols = False
        choice_list = tuple((color_choice, color_choice) for color_choice in color_choices)
        widget = forms.Select(attrs={'class': 'form-select'})
        self.fields['your_color'] = forms.ChoiceField(label='Your Color', choices=choice_list, initial=selected_colors[0], widget=widget)
        for i in range(1, 7):
            other_player_color_field = f'other_player_color_{i}'
            other_player_color_label = f'Other Player Color {i}'
            widget = forms.Select(attrs={'class': 'form-select'})
            self.fields[other_player_color_field] = forms.ChoiceField(label=other_player_color_label, choices=choice_list, initial=selected_colors[i], widget=widget)
        self.fields['symbols'] = forms.BooleanField(required=False, label='Symbols on Player Tokens (corresponding to color)', initial=symbols, widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))

    def clean(self):
        if 'your_color' in self.cleaned_data:
            selected_colors = [self.cleaned_data['your_color']]
            for i in range(1, 7):
                other_player_color_field = f'other_player_color_{i}'
                color = self.cleaned_data[other_player_color_field]
                if color in selected_colors:
                    self.add_error(other_player_color_field, 'All selected colors must be unique.')
                selected_colors.append(color)
            for color in color_choices:
                if color not in selected_colors:
                    selected_colors.append(color)
            self.cleaned_data['colors'] = ', '.join(selected_colors)
        return self.cleaned_data
