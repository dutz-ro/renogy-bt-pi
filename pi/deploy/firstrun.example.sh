#!/bin/bash
# Prim boot pentru Raspberry Pi OS Lite (armhf).
#
# Se copiază pe partiția boot ca "firstrun.sh" și se rulează o singură dată,
# prin systemd.run din cmdline.txt:
#
#   systemd.run=/boot/firmware/firstrun.sh
#
# Completează valorile de mai jos înainte de a porni Pi-ul.
# NU comite acest fișier cu parole reale.
set -euxo pipefail

BOOT=/boot/firmware
LOG="$BOOT/firstrun.log"

mv -f "$BOOT/firstrun.sh" "$BOOT/.firstrun.ran" 2>/dev/null || true

# Scoate systemd.run ca sistemul să pornească normal la următoarele boot-uri.
sed -i -E 's/[[:space:]]+systemd\.(run|run_success_action|unit)=[^[:space:]]+//g' \
    "$BOOT/cmdline.txt"

exec >>"$LOG" 2>&1
echo "=== firstrun $(date -Is) ==="

# --------------------------------------------------------------------------
# Editează aici
# --------------------------------------------------------------------------
NEWUSER=pi
NEWPASS='<PAROLA_PI>'
NEWHOST=renogy-pi
TZ=Europe/Bucharest

# SSID-ul trebuie să fie WPA2 clasic. Pi Zero W nu acceptă WPA2/WPA3 mixed.
WIFI_MAIN_SSID='<SSID_PRINCIPAL>'
WIFI_MAIN_PSK='<PAROLA_WIFI_PRINCIPAL>'
WIFI_BACKUP_SSID='<SSID_REZERVA>'
WIFI_BACKUP_PSK='<PAROLA_WIFI_REZERVA>'
# --------------------------------------------------------------------------

NM_DIR=/etc/NetworkManager/system-connections

echo "$NEWHOST" >/etc/hostname
if grep -q '^127\.0\.1\.1' /etc/hosts; then
    sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t$NEWHOST/" /etc/hosts
else
    printf '127.0.1.1\t%s\n' "$NEWHOST" >>/etc/hosts
fi
hostname "$NEWHOST" || true

if ! id "$NEWUSER" >/dev/null 2>&1; then
    useradd -m -s /bin/bash \
        -G sudo,adm,dialout,cdrom,audio,video,plugdev,games,users,input,netdev,gpio,i2c,spi \
        "$NEWUSER"
fi
usermod -s /bin/bash "$NEWUSER"
usermod -aG sudo,adm,dialout,netdev,gpio,i2c,spi "$NEWUSER"
echo "$NEWUSER:$NEWPASS" | chpasswd

sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
ln -sf "/usr/share/zoneinfo/$TZ" /etc/localtime
echo "$TZ" >/etc/timezone

mkdir -p "$NM_DIR"
write_profile() {
    local file="$1" id="$2" ssid="$3" psk="$4" priority="$5"
    cat >"$NM_DIR/$file" <<EOF
[connection]
id=$id
type=wifi
autoconnect=true
autoconnect-retries=0
autoconnect-priority=$priority

[wifi]
mode=infrastructure
ssid=$ssid

[wifi-security]
key-mgmt=wpa-psk
psk=$psk

[ipv4]
method=auto

[ipv6]
method=auto
EOF
    chmod 600 "$NM_DIR/$file"
    chown root:root "$NM_DIR/$file"
}

write_profile "renogy-main.nmconnection" "renogy-main" "$WIFI_MAIN_SSID" "$WIFI_MAIN_PSK" 200
write_profile "renogy-backup.nmconnection" "renogy-backup" "$WIFI_BACKUP_SSID" "$WIFI_BACKUP_PSK" 100

mkdir -p /etc/NetworkManager/conf.d
printf '[connection]\nwifi.powersave = 2\n' >/etc/NetworkManager/conf.d/wifi-powersave.conf

# Unblock radio la fiecare boot (unele imagini pornesc cu WiFi soft-blocked).
mkdir -p /etc/systemd/system/multi-user.target.wants
cat >/etc/systemd/system/renogy-rfkill.service <<'EOF'
[Unit]
Description=Unblock wireless devices
Before=NetworkManager.service

[Service]
Type=oneshot
ExecStart=/usr/sbin/rfkill unblock all
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
ln -sf /etc/systemd/system/renogy-rfkill.service \
    /etc/systemd/system/multi-user.target.wants/renogy-rfkill.service

# SSH și NetworkManager activate prin symlink (fără systemctl în timpul boot-ului).
ln -sf /lib/systemd/system/ssh.service \
    /etc/systemd/system/multi-user.target.wants/ssh.service
ln -sf /lib/systemd/system/NetworkManager.service \
    /etc/systemd/system/multi-user.target.wants/NetworkManager.service

# Consolă pe USB serial, utilă când nu ai rețea.
mkdir -p /etc/systemd/system/getty.target.wants
ln -sf /lib/systemd/system/serial-getty@.service \
    /etc/systemd/system/getty.target.wants/serial-getty@ttyGS0.service

sync
echo "=== firstrun done $(date -Is) ==="
