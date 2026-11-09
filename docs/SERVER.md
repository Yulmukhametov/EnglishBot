# Установка на Ubuntu 24.04

Для одного пользователя: VPS с 2 ГБ памяти, 1–2 ядрами, обычная Ubuntu 24.04.
Этот вариант использует OpenAI API. Аренда сервера не включает расходы AI.
SSH-пароль, Telegram-токен, ключ OpenAI вводятся только на вашем компьютере/сервере, не в чат.

## 1. Подключение

В терминале вашего устройства выполните, заменив `IP_СЕРВЕРА` публичным адресом из панели:

```sh
ssh root@IP_СЕРВЕРА
```

Если провайдер указал другой SSH-порт, используйте `ssh -p ПОРТ root@IP_СЕРВЕРА`.
Если выдан другой пользователь, используйте его вместо root и добавляйте `sudo` к командам установки.
При первом подключении сверяйте SSH-отпечаток с консолью провайдера, если он доступен.
Пароль при вводе не отображается. Если выданы SSH-ключи, используйте указанную провайдером инструкцию.

Проверьте ОС и ресурсы:

```sh
cat /etc/os-release
free -h
df -h /
```

## 2. Установка

Следующие команды выполняются ВНУТРИ сервера. Они устанавливают зависимости и создают отдельного
системного пользователя `englishbot`. В `/tmp/englishbot-setup` не должно быть ваших файлов.
Бот не стартует автоматически до ввода настроек.

```sh
sudo apt-get update
sudo apt-get install -y git
 git clone https://github.com/Yulmukhametov/EnglishBot.git /tmp/englishbot-setup
sudo bash /tmp/englishbot-setup/deploy/install-ubuntu.sh
```

Если `/tmp/englishbot-setup` уже существует от предыдущей установки, не удаляйте его вслепую:
проверьте содержимое или клонируйте в другую новую папку и измените команду запуска скрипта.
Установщик не удаляет существующую базу или `.env` и не обновляет существующую копию из GitHub.

## 3. Ключи

Создайте Telegram-бота у @BotFather (`/newbot`) и ключ OpenAI в кабинете API Platform.
Убедитесь, что API-аккаунт доступен для вас, модели разрешены и есть средства/кредиты.
Узнайте свой числовой Telegram ID, например через @userinfobot.

Откройте настройки прямо на сервере:

```sh
sudo nano /opt/englishbot/app/.env
```

Заполните `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `OWNER_ID`.
Для первых испытаний можно поставить `DAILY_TURN_LIMIT=10`.
Сохранение в nano: Ctrl+O, Enter; выход: Ctrl+X.
Не вставляйте ключи в команды shell и не публикуйте содержимое файла.
Права после настройки:

```sh
sudo chown englishbot:englishbot /opt/englishbot/app/.env
sudo chmod 600 /opt/englishbot/app/.env
```

## 4. Запуск и проверка

```sh
sudo systemctl enable --now englishbot
sudo systemctl status englishbot --no-pager
```

В статусе ожидается `active (running)`. Затем отправьте `/start` в Telegram.
Если бот молчит и нет ошибок, убедитесь, что OWNER_ID соответствует вашему аккаунту.
Не запускайте второго бота с тем же токеном параллельно.
При проблеме проверьте журнал локально:

```sh
sudo journalctl -u englishbot -n 50 --no-pager
```

Перед отправкой кому-либо удалите из журнала токены, URL с токенами, имена и личные данные.
Если сервис исчерпал попытки запуска после исправления настроек:

```sh
sudo systemctl reset-failed englishbot
sudo systemctl restart englishbot
```

Теперь можно закрыть SSH: бот работает под отдельным пользователем и запускается после перезагрузки VPS.
Остановка: `sudo systemctl stop englishbot`. Отключение автозапуска: `sudo systemctl disable englishbot`.
Бот сам обращается в Telegram, поэтому открывать входящий веб-порт или покупать домен не требуется.

## 5. Обновление и резервная копия

Перед обновлением остановите сервис и сохраните `/opt/englishbot/app/data/mentor.sqlite3`
в защищённое место. Копируйте базу при остановленном сервисе; `.env` храните отдельно как секрет.
Затем:

```sh
sudo systemctl stop englishbot
sudo -u englishbot git -C /opt/englishbot/app pull --ff-only
sudo -u englishbot /opt/englishbot/app/.venv/bin/python -m pip install -e /opt/englishbot/app
sudo systemctl restart englishbot
```

Если меняется файл `deploy/englishbot.service`, дополнительно скопируйте его в
`/etc/systemd/system/englishbot.service`, выполните `sudo systemctl daemon-reload` и перезапустите сервис.
Настройте собственную регулярную резервную копию: в первой версии расписания бэкапов нет.

Проверки качества после запуска: [TESTING.md](TESTING.md).
