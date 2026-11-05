# Бот 24/7 на бесплатном сервере Google Cloud (e2-micro, Free Tier)

1. https://console.cloud.google.com → создать проект → Compute Engine → VM instances → Create instance:
   * Region: us-central1 (или us-west1 / us-east1) — только эти входят в бесплатный тариф
   * Machine type: e2-micro
   * Boot disk: Ubuntu 24.04 LTS, Standard persistent disk, 30 GB
2. Нажмите кнопку **SSH** рядом с сервером — откроется терминал в браузере.
3. В окне SSH: ⚙ → **Upload file** → выберите `helpdesk-bot-server.zip` с рабочего стола.
4. Вставьте команды:
   sudo apt-get update -y && sudo apt-get install -y unzip
   unzip -o helpdesk-bot-server.zip -d it-helpdesk-bot && cd it-helpdesk-bot && bash deploy/install.sh
5. Остановите бота на ноутбуке (закройте окно «IT Helpdesk Bot»): с одним токеном может работать только один бот.

Бот запустится как служба systemd, будет перезапускаться при сбоях и после перезагрузки сервера.
Логи: `journalctl -u helpdesk-bot -f`, перезапуск: `sudo systemctl restart helpdesk-bot`.
(Подойдёт и Oracle Cloud Always Free — шаги те же, начиная с п.3, файл копируется через scp.)
