# TabonyGames
Website for the game(s) implemented by Charles J. Tabony (Logitude)

## Steps to run locally

1. This repo requires the nations repo: https://github.com/Logitude/nations to be either cloned into or linked into `Nations/nations`.
    For example, if you clone the two repos into the same directory, then in the root of `TabonyGames`, run:

    `ln -s ../../nations Nations/nations`

2. You can download the images from https://drive.google.com/file/d/1bv3S7fQ0iSGRGm9gqwnoeL9Uqh_pVNLa/view?usp=drive_link

    and then, with your current directory in the root of the repo:

    `tar -xzf TGstatic.tar.gz`

3. You'll need what's in `requirements.txt` in your Python environment.

4. Copy `local/example_secrets.ini` to `local/secrets.ini`.
    The values in there are not important, as long as you don't change the `secret_key` after you've created the database.

5. Create the initial database:

    `DJANGO_LOCAL_RUN=TRUE python manage.py migrate`

    `DJANGO_LOCAL_RUN=TRUE python manage.py createsuperuser` (Remember your username and password.)

6. Run `daphne` to start the server. `daphne` is listed in the `requirements.txt`.

    `DJANGO_LOCAL_RUN=TRUE daphne -b 127.0.0.1 Games.asgi:application`

7. With `daphne` running, you should be able to navigate to the page in a browser: http://127.0.0.1:8000

8. To mess with stuff in the database, go to: http://127.0.0.1:8000/admin/
