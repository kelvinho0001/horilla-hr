# Deploying Demiller Farms HRM on cPanel

Horilla (Django) + the four Demiller apps (`biometric_integration`, `attendance_processing`, `weekly_payroll`,
`bank_export`) running as a cPanel **Python App** (Passenger) with a **PostgreSQL** database.

> **Honesty note.** This guide follows cPanel's standard "Setup Python App" workflow and this repository's own settings.
> It has **not** been tried on your hosting account. Section 1 lists what must be true on your host; check it first.
> If any item fails, use a VPS with the Docker setup already in this repo (`docker-compose.prod.yml`) instead.

---

## 1. Can your cPanel host run it? (check before anything else)

| Need | Why | How to check |
|---|---|---|
| **Setup Python App** in cPanel (CloudLinux Passenger) | runs Django | cPanel > Software > *Setup Python App* exists |
| **Python 3.12 or newer** selectable there | Horilla needs 3.12-3.14 | the Python version dropdown |
| **PostgreSQL 14 or newer** (*PostgreSQL Databases* in cPanel), **or** accept SQLite (section 3b) | Horilla/Django 5.2 supports PostgreSQL 14+ only; MySQL/MariaDB is **not supported**. Many cPanel hosts run PostgreSQL 9.x-13, which is too old (error: `PostgreSQL 14 or later is required`) | cPanel > PostgreSQL Databases, or in Terminal `psql --version` |
| **SSH / Terminal** | install packages, run migrations | cPanel > Terminal (or SSH access enabled) |
| **~1 GB RAM / process limit** during install | `spacy`, `PyMuPDF`, `pandas` are large; CloudLinux can kill `pip` | ask the host, or see 6.1 |
| Outbound HTTPS to `github.com` and `pypi.org` | one dependency (spaCy model) downloads from GitHub | from Terminal: `curl -sI https://github.com` |
| **Cron Jobs** | nightly backup | cPanel > Advanced |
| **AutoSSL / a certificate** | the app handles pay and bank data: HTTPS only | cPanel > SSL/TLS Status |

Shared hosting is a poor fit for payroll software (resource limits, no background processes, shared access).
It is workable for a small team, but a small VPS is the better long-term home.

## 2. Plan the names

Use a subdomain, e.g. `hr.demillerfarms.com` (not a folder under the main site). Keep the code **outside** `public_html`.
Below, `CPUSER` is your cPanel username.

## 3. Create the database

> **First check the PostgreSQL version your host runs (14 or newer is required).** If it is older, skip to **3b (SQLite)**.

cPanel > **PostgreSQL Databases**:
1. Create a database, e.g. `horilla` (cPanel adds the prefix: `CPUSER_horilla`).
2. Create a user, e.g. `hrapp` (`CPUSER_hrapp`) with a long random password.
3. **Add user to database** with *All privileges*.

Your connection string (`DATABASE_URL`) is:

```
postgres://CPUSER_hrapp:URL-ENCODED-PASSWORD@localhost:5432/CPUSER_horilla
```

Avoid `@ : / # ?` in the password, or URL-encode them (`@` -> `%40`). Use `localhost` unless your host gives another host name.

### 3b. No PostgreSQL 14+? Use SQLite (small teams)

SQLite needs no database server. It is a reasonable choice for one company with a handful of HR users working at the same time;
it is not meant for many simultaneous users. Do **not** set `DATABASE_URL`. Instead set these (in Setup Python App > Environment
variables **and** `export` them in Terminal before running `manage.py` commands):

```
DB_ENGINE=django.db.backends.sqlite3
DB_NAME=/home/CPUSER/hrm_data/horilla.sqlite3
```

```bash
mkdir -p ~/hrm_data && chmod 700 ~/hrm_data
```

Keep the file **outside** the code folder and outside `public_html`. Back it up with `SQLITE_PATH` (section 10). Make sure the terminal and the
website use the **same** `DB_NAME`: if you run `migrate` without the variables, Django silently creates a different file (`TestDB.sqlite3` in the app folder).

## 4. Get the code onto the server

Preferred (cPanel > **Git Version Control** > *Create*): clone your repository into `/home/CPUSER/horilla`.
Or over SSH:

```bash
cd ~
git clone https://github.com/kelvinho0001/horilla-hr.git horilla
cd horilla
git checkout main          # deploy a reviewed branch or a tagged release, never a work-in-progress branch
```

For a private repository, add the server's SSH public key as a read-only **deploy key** on GitHub.
Tag what you deploy (`git tag -a release-YYYY-MM-DD -m "..."`) so you can roll back to it.

## 5. Create the Python app

cPanel > **Setup Python App** > *Create Application*:

| Field | Value |
|---|---|
| Python version | 3.12 or newer |
| Application root | `horilla` |
| Application URL | `hr.demillerfarms.com` |
| Application startup file | `passenger_wsgi.py` (already in the repo) |
| Application Entry point | `application` |

Save. cPanel shows a command like `source /home/CPUSER/virtualenv/horilla/3.12/bin/activate && cd /home/CPUSER/horilla`.
**Copy it**: you run it at the start of every Terminal session below.

## 6. Install the dependencies

In Terminal, paste the command from step 5, then:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 6.1 If the install is killed or fails
* `Killed` / out of memory: ask the host to lift the memory/process limit temporarily, or install heavy packages first one by one:
  `pip install spacy pandas PyMuPDF`, then `pip install -r requirements.txt`.
* A package tries to compile and the compiler is missing: use a Python version that has pre-built wheels (3.12 or 3.13), or ask the host.
* `en_core_web_sm` cannot download: the host blocks GitHub. Download the wheel elsewhere and `pip install` the file.
* `psycopg2` errors: `requirements.txt` uses `psycopg2-binary`, which needs no PostgreSQL headers. If it still fails the Python build is unusual; ask the host.

## 7. Configure the environment

In **Setup Python App** > your app > *Environment variables*, add each (no quotes):

| Variable | Value |
|---|---|
| `DJANGO_SETTINGS_MODULE` | `horilla.settings` |
| `HORILLA_ENV` | `production` |
| `DEBUG` | `False` |
| `SECRET_KEY` | a long random string (below) |
| `DB_INIT_PASSWORD` | another long random string (Horilla refuses to start in production without a real one) |
| `ALLOWED_HOSTS` | `hr.demillerfarms.com` |
| `CSRF_TRUSTED_ORIGINS` | `https://hr.demillerfarms.com` |
| `DATABASE_URL` | the string from step 3 (PostgreSQL 14+ only; leave it **unset** for SQLite and set `DB_ENGINE`/`DB_NAME` instead) |
| `TIME_ZONE` | `Africa/Lagos` |
| `SECURE_SSL_REDIRECT` | `True` (after HTTPS works; set `False` for the very first test if needed) |
| `PAYROLL_COMPANY_NAME` | optional, used in the default bank narration |

Generate secrets in Terminal:

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

Never commit these values. The repository contains only `.env.dist` placeholders.
Leave `HORILLA_TRUSTED_PROXIES` **unset** unless you put a proxy such as Cloudflare in front; with Passenger the app normally
sees the real visitor address (verify by checking that a lockout after repeated bad logins affects one client, not everyone).

## 8. Initialise

```bash
cd ~/horilla
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py load_banks docs/demiller/bank_codes.csv
python manage.py createhorillauser --first_name Admin --last_name User --username admin \
       --password 'CHOOSE-A-STRONG-ONE' --email you@demillerfarms.com --phone 08000000000
python manage.py demiller_check
```

`migrate` also creates the role groups (HR Officer, HR/Payroll Approver, Management, Accountant, IT Administrator).
`demiller_check` must end with **0 failures**; read the warnings. Static files are served by the app itself (WhiteNoise),
so no Apache configuration is needed for them.

Then restart: Setup Python App > **Restart** (or `touch ~/horilla/tmp/restart.txt`) and open `https://hr.demillerfarms.com/`.
Complete Horilla's first-run screens if shown (company, first user).

## 9. Set up people and roles

1. In Horilla add the employees, departments and worker categories (employee types).
2. Give each staff member their group **for the company**: Horilla only applies a group's permissions in a company where the
   user has been assigned that group. Do not put everyone in the approver group. See `docs/demiller/PRODUCTION_RUNBOOK.md`.
3. Create an Attendance Rule for the real attendance window and working days (Attendance > Weekly Attendance Review > Rules).
4. Add each worker's pay profile, bank and account; map biometric IDs (Attendance > Biometric Import).

## 10. Backups (do this before real data goes in)

```bash
mkdir -p ~/backups
chmod +x ~/horilla/scripts/backup.sh
```

cPanel > **Cron Jobs** > add (daily at 02:10; set `MAILTO` so failures are emailed):

```
10 2 * * * DATABASE_URL='postgres://CPUSER_hrapp:PASSWORD@localhost:5432/CPUSER_horilla' BACKUP_DIR=$HOME/backups MEDIA_DIR=$HOME/horilla/media KEEP_DAYS=30 bash $HOME/horilla/scripts/backup.sh
```

SQLite instead (the script needs `python3`; use the app's virtualenv python):

```
10 2 * * * PATH=/home/CPUSER/virtualenv/APPROOT/3.12/bin:$PATH SQLITE_PATH=$HOME/hrm_data/horilla.sqlite3 BACKUP_DIR=$HOME/backups KEEP_DAYS=30 bash $HOME/horilla/scripts/backup.sh
```

Then copy `~/backups` **off the server** regularly (another host, cloud storage, or your own computer). A backup that lives only
on the same server does not survive losing the server. Test a restore once (runbook).
The cPanel account backup is a useful extra, not a substitute.

## 11. What will not work on cPanel

Horilla's own scheduler (`python manage.py run_scheduler`) is a long-running process and cPanel cannot keep one alive.
The Demiller workflow (upload attendance, review, payroll, bank export) does **not** need it. Horilla features that rely on it
(automatic payslip generation, Horilla's built-in database backup job, scheduled notifications, direct device polling) will not run.
`/ready/` is not used. Uploaded files (`media/`) are not served by Django; profile pictures may not display unless you add an
Apache alias for `/media/` to the media folder, which makes those files reachable by URL. Do not put sensitive documents there.

## 12. Updating and rolling back

Before any update: run the backup script by hand and note the current tag (`git describe --tags`).

```bash
cd ~/horilla && git fetch --tags && git checkout release-YYYY-MM-DD
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py demiller_check
touch tmp/restart.txt
```

Roll back: check out the previous tag, `pip install -r requirements.txt`, restart. **If the update ran migrations, also restore
the pre-update database backup** (migrations are not reversed automatically). Never deploy an upstream Horilla change straight to
production: test it on a staging copy first (runbook, "Upgrading").

## 13. Troubleshooting

| Symptom | Likely cause / action |
|---|---|
| Blank page or "Incomplete response" | see the error log: Setup Python App shows the log path, or `~/logs/`, or `stderr.log` in the app root |
| `DisallowedHost` | `ALLOWED_HOSTS` does not match the exact host name |
| "CSRF verification failed" | `CSRF_TRUSTED_ORIGINS` missing the `https://` origin |
| "SECRET_KEY ... placeholder" / refuses to start | set real `SECRET_KEY`, `ALLOWED_HOSTS`, `DB_INIT_PASSWORD` |
| `could not connect to server` | wrong `DATABASE_URL` (prefix, password encoding, host) |
| `PostgreSQL 14 or later is required (found 10.x)` | the host's PostgreSQL is too old; use SQLite (section 3b), a newer database server, or a VPS |
| Site works in the browser but data/users from Terminal are missing (or vice versa) | the terminal and the website use different databases; compare the `DATABASE_URL` / `DB_NAME` values in both |
| Page unstyled | run `collectstatic`, restart |
| Redirect loop after enabling HTTPS redirect | AutoSSL not active yet; set `SECURE_SSL_REDIRECT=False`, fix SSL, re-enable |
| Changes not visible | restart the app after every code or environment change |

## 14. Before go-live

Run `python manage.py demiller_check`; complete `docs/demiller/UAT_CHECKLIST.md`; confirm a backup exists and a restore was tested;
confirm only the intended people hold the approver, Management and Accountant groups; change the default admin password.
