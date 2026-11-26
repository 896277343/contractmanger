#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/contractmanger"
BACKUP_DIR="/opt/contractmanger-backups"
SERVICE="contractmanger"
PYTHON="$APP_DIR/.venv312/bin/python"
UV="$APP_DIR/.venv/bin/uv"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "请以 root 身份运行：sudo bash scripts/update_server.sh" >&2
    exit 1
fi
if [[ ! -d "$APP_DIR/.git" ]]; then
    echo "服务器项目尚未通过 Git 克隆；先完成首次代码仓库接入。" >&2
    exit 1
fi
if [[ ! -f "$APP_DIR/.env" || ! -f "$APP_DIR/data/contracts.sqlite3" ]]; then
    echo "缺少生产环境配置或 SQLite 数据库，已停止更新。" >&2
    exit 1
fi
if [[ ! -x "$PYTHON" || ! -x "$UV" ]]; then
    echo "找不到现有 Python 3.12 或 uv 环境，已停止更新。" >&2
    exit 1
fi
if [[ -n "$(sudo -u contractapp git -C "$APP_DIR" status --porcelain --untracked-files=no)" ]]; then
    echo "服务器代码有未提交修改，已停止更新，避免覆盖。" >&2
    exit 1
fi

install -d -o contractapp -g contractapp -m 700 "$BACKUP_DIR"
sudo -u contractapp bash -c 'set -a; . /opt/contractmanger/.env; set +a; cd /opt/contractmanger; .venv312/bin/python scripts/backup_sqlite.py /opt/contractmanger-backups'
sudo -u contractapp git -C "$APP_DIR" pull --ff-only origin main
sudo -u contractapp "$UV" pip install --python "$PYTHON" -r "$APP_DIR/requirements.txt" gunicorn
if ! sudo -u contractapp "$PYTHON" -c 'import xlrd' >/dev/null 2>&1; then
    SITE_PACKAGES="$(sudo -u contractapp "$PYTHON" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
    if [[ "$SITE_PACKAGES" != "$APP_DIR/.venv312/"* ]]; then
        echo "Python site-packages 路径异常：$SITE_PACKAGES" >&2
        exit 1
    fi
    sudo -u contractapp "$APP_DIR/.venv/bin/python" -m pip install --target "$SITE_PACKAGES" 'xlrd>=2.0.2,<3'
fi
sudo -u contractapp "$PYTHON" -c 'import django, openpyxl, bs4, xlrd'
sudo -u contractapp bash -c 'set -e; set -a; . /opt/contractmanger/.env; set +a; cd /opt/contractmanger; .venv312/bin/python manage.py migrate --noinput; .venv312/bin/python manage.py collectstatic --noinput; .venv312/bin/python manage.py check --deploy'
systemctl restart "$SERVICE"
systemctl is-active --quiet "$SERVICE"
echo "代码更新完成，服务运行中；数据库备份位于 $BACKUP_DIR。"
