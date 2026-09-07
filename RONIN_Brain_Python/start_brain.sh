#!/data/data/com.termux/files/usr/bin/bash
set -e

brain_workdir="/mnt/sdcard/terai/RONIN_Workspace/RONIN_Brain_Python"
pid_file="$brain_workdir/.brain.pid"
startup_lock="$brain_workdir/.brain.starting"

is_uvicorn_pid() {
    local candidate_pid="$1"
    [ -n "$candidate_pid" ] || return 1
    [ -r "/proc/$candidate_pid/cmdline" ] || return 1
    local command_line
    command_line="$(tr '\0' ' ' < "/proc/$candidate_pid/cmdline" 2>/dev/null || true)"
    [[ "$command_line" == *uvicorn* ]] \
        && [[ "$command_line" == *main:app* ]] \
        && [[ "$command_line" == *"--port 8000"* || "$command_line" == *"--port=8000"* ]]
}

# The shell PID stays alive while uvicorn runs, so concurrent launch requests
# cannot start a second instance. Stale PIDs are safely discarded.
if [ -f "$pid_file" ]; then
    existing_pid="$(cat "$pid_file" 2>/dev/null || true)"
    if is_uvicorn_pid "$existing_pid"; then
        exit 0
    fi
    rm -f "$pid_file"
fi

# Keep startup serialization atomic while the uvicorn PID is being created.
if [ -f "$startup_lock" ]; then
    existing_start_pid="$(cat "$startup_lock" 2>/dev/null || true)"
    if [ -n "$existing_start_pid" ] && kill -0 "$existing_start_pid" 2>/dev/null; then
        exit 0
    fi
    rm -f "$startup_lock"
fi
(set -o noclobber; echo "$$" > "$startup_lock") 2>/dev/null || exit 0

uvicorn_pid=""
cleanup() {
    if [ -n "$uvicorn_pid" ] && [ "$(cat "$pid_file" 2>/dev/null || true)" = "$uvicorn_pid" ]; then
        rm -f "$pid_file"
    fi
    rm -f "$startup_lock"
}
trap cleanup EXIT INT TERM

# Also cover a Brain started outside this script. Only a healthy FastAPI Brain
# prevents another launch; unrelated services on port 8000 do not.
if curl --fail --silent --show-error --max-time 2 http://127.0.0.1:8000/health \
    | grep --quiet --extended-regexp '"status"[[:space:]]*:[[:space:]]*"ok"'; then
    exit 0
fi

cd "$brain_workdir"
uvicorn main:app --host 0.0.0.0 --port 8000 &
uvicorn_pid="$!"
printf '%s\n' "$uvicorn_pid" > "$pid_file"
wait "$uvicorn_pid"
