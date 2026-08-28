#!/usr/bin/env bash
#
# Get the Email Assistant running on a Mac, from nothing.
#
#   curl -fsSL <raw-url>/scripts/bootstrap_macos.sh | bash
# or, after cloning:
#   ./scripts/bootstrap_macos.sh
#
# Safe to run more than once: every step checks before it acts.

set -uo pipefail

REPO_URL="${REPO_URL:-https://github.com/LuckyFox973/asiceopen-samples.git}"
BRANCH="${BRANCH:-claude/gmail-ai-assistant-system-u72j2z}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/email-assistant}"
PG_VERSION="${PG_VERSION:-16}"
DB_NAME="email_assistant"
TEST_DB_NAME="email_assistant_test"
DB_USER="eaa"
DB_PASSWORD="devpassword"

bold=$(printf '\033[1m'); dim=$(printf '\033[2m'); red=$(printf '\033[31m')
green=$(printf '\033[32m'); yellow=$(printf '\033[33m'); reset=$(printf '\033[0m')

step()  { printf "\n%s==> %s%s\n" "$bold" "$1" "$reset"; }
ok()    { printf "    %s✓%s %s\n" "$green" "$reset" "$1"; }
warn()  { printf "    %s!%s %s\n" "$yellow" "$reset" "$1"; }
die()   { printf "\n%sStopped:%s %s\n\n" "$red" "$reset" "$1" >&2; exit 1; }

# ---------------------------------------------------------------------------

step "Checking this is a Mac"
[ "$(uname -s)" = "Darwin" ] || die "This script is for macOS. On Linux, follow docs/SETUP.md."
ok "macOS $(sw_vers -productVersion)"

step "Homebrew"
if command -v brew >/dev/null 2>&1; then
    ok "already installed"
else
    warn "installing Homebrew — it will ask for your password"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
        || die "Homebrew installation failed. Install it manually from https://brew.sh and run this again."
    # Apple Silicon puts brew somewhere the shell does not yet know about.
    for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        [ -x "$candidate" ] && eval "$("$candidate" shellenv)"
    done
    command -v brew >/dev/null 2>&1 || die "Homebrew installed but is not on PATH. Open a new terminal and run this again."
    ok "installed"
fi

step "Python, PostgreSQL $PG_VERSION, pgvector, git"
for formula in "python@3.12" "postgresql@${PG_VERSION}" pgvector git; do
    if brew list --formula "$formula" >/dev/null 2>&1; then
        ok "$formula"
    else
        printf "    installing %s…\n" "$formula"
        brew install "$formula" >/dev/null 2>&1 || die "brew install $formula failed. Run it yourself to see why."
        ok "$formula installed"
    fi
done

PG_BIN="$(brew --prefix)/opt/postgresql@${PG_VERSION}/bin"
[ -d "$PG_BIN" ] || die "PostgreSQL $PG_VERSION is not where expected ($PG_BIN)."
export PATH="$PG_BIN:$PATH"

step "Starting PostgreSQL"
if pg_isready -q 2>/dev/null; then
    ok "already running"
else
    brew services start "postgresql@${PG_VERSION}" >/dev/null 2>&1
    for _ in $(seq 1 30); do pg_isready -q 2>/dev/null && break; sleep 1; done
    pg_isready -q 2>/dev/null || die "PostgreSQL did not start. Try: brew services restart postgresql@${PG_VERSION}"
    ok "running"
fi

step "Databases"
psql -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" 2>/dev/null | grep -q 1 \
    || psql -d postgres -c "CREATE ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASSWORD}' CREATEDB;" >/dev/null 2>&1
ok "role ${DB_USER}"

for db in "$DB_NAME" "$TEST_DB_NAME"; do
    if psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${db}'" 2>/dev/null | grep -q 1; then
        ok "database ${db} (already there)"
    else
        psql -d postgres -c "CREATE DATABASE ${db} OWNER ${DB_USER};" >/dev/null 2>&1 \
            || die "Could not create database ${db}."
        ok "database ${db}"
    fi
    psql -d "$db" -c "CREATE EXTENSION IF NOT EXISTS pg_trgm; CREATE EXTENSION IF NOT EXISTS unaccent; CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null 2>&1 \
        || warn "could not enable all extensions in ${db} — pgvector is only needed later"
done
ok "extensions enabled"

step "The project"
if [ -d "$INSTALL_DIR/.git" ]; then
    ok "already cloned at $INSTALL_DIR"
    git -C "$INSTALL_DIR" fetch origin "$BRANCH" >/dev/null 2>&1 \
        && git -C "$INSTALL_DIR" checkout "$BRANCH" >/dev/null 2>&1 \
        && git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH" >/dev/null 2>&1 \
        && ok "updated to latest" \
        || warn "could not update — you have local changes, which is fine"
else
    git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR" >/dev/null 2>&1 \
        || die "Could not clone $REPO_URL. If it is private, run: git clone $REPO_URL $INSTALL_DIR"
    ok "cloned to $INSTALL_DIR"
fi

PROJECT="$INSTALL_DIR/email-assistant"
[ -d "$PROJECT" ] || die "Expected the project at $PROJECT but it is not there."
cd "$PROJECT" || die "Could not enter $PROJECT"

step "Python environment"
PYTHON="$(brew --prefix)/opt/python@3.12/bin/python3.12"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "Python 3.11 or newer is required; found $("$PYTHON" --version)."

[ -d .venv ] || "$PYTHON" -m venv .venv || die "Could not create the virtual environment."
ok "virtualenv"

printf "    installing dependencies (a minute or two)…\n"
./.venv/bin/pip install --quiet --upgrade pip >/dev/null 2>&1
./.venv/bin/pip install --quiet -e ".[dev]" >/dev/null 2>&1 \
    || die "Dependency installation failed. Run: ./.venv/bin/pip install -e '.[dev]'"
ok "dependencies"

step "Configuration"
if [ -f .env ]; then
    ok ".env already exists — leaving it alone"
else
    cp .env.example .env
    TOKEN_KEY="$(./.venv/bin/python -m app.core.crypto keygen)"
    BACKUP_KEY="$(./.venv/bin/python -m app.core.crypto keygen)"
    # BSD sed needs the empty -i argument; GNU sed does not. This is macOS.
    sed -i '' "s|^TOKEN_ENCRYPTION_KEY=.*|TOKEN_ENCRYPTION_KEY=${TOKEN_KEY}|" .env
    sed -i '' "s|^BACKUP_ENCRYPTION_KEY=.*|BACKUP_ENCRYPTION_KEY=${BACKUP_KEY}|" .env
    sed -i '' "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@127.0.0.1:5432/${DB_NAME}|" .env
    sed -i '' "s|^TEST_DATABASE_URL=.*|TEST_DATABASE_URL=postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@127.0.0.1:5432/${TEST_DB_NAME}|" .env
    ok ".env created, encryption keys generated"
fi

step "Database schema"
./.venv/bin/alembic upgrade head >/dev/null 2>&1 \
    || die "Migrations failed. Run: ./.venv/bin/alembic upgrade head"
ok "schema up to date"

step "Checking it works"
if ./.venv/bin/python -m pytest -q >/dev/null 2>&1; then
    ok "all tests pass"
else
    warn "some tests failed — the system may still work; run ./.venv/bin/python -m pytest to see"
fi

./.venv/bin/python scripts/demo_seed.py --reset >/dev/null 2>&1 && ok "demo data loaded"

# ---------------------------------------------------------------------------

cat <<DONE

${bold}Done. The project lives at:${reset}
    ${PROJECT}

${bold}Try it now, with the demo data:${reset}
    cd ${PROJECT}
    ./.venv/bin/python -m app.cli stats
    ./.venv/bin/python -m app.cli find "CMR duplicitne"

${bold}Connect Claude to it:${reset}
    claude mcp add email-assistant -- ${PROJECT}/.venv/bin/python -m app.mcp.server

${bold}Next: connect your real mailbox.${reset}
    That needs a Google Cloud project — follow docs/GOOGLE_SETUP.md.
    Before you start, decide whether the assistant may change the mailbox:

    ${dim}open ${PROJECT}/.env and set GMAIL_WRITE_ENABLED=true${reset}

    then run this to see exactly which scopes to paste into Google:

    ${dim}./.venv/bin/python -m app.cli check${reset}

DONE
