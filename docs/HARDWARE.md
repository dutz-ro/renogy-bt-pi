# Fișă de hardware și protocol

Tot ce e în documentul ăsta a fost citit de pe hardware real, nu presupus.

## 1. Calculatoare și rețea

| Element | Valoare |
|---|---|
| Placa | Raspberry Pi Zero W Rev 1.1 (ARMv6, 512 MB) |
| Sistem | Raspberry Pi OS Lite 32-bit (Trixie, armhf), kernel 6.18 |
| Hostname / user | `renogy-pi` / `pi` |
| Rol | central BLE, polling secvențial la 60 s |
| Rețea | WiFi `RUT241_C08E` (WPA2), fallback `SmartBox` |
| Acces | Tailscale + SSH cu cheie; consolă USB pe `ttyGS0` |

> Pi Zero W **nu suportă** rețele WPA2/WPA3 „transition" (PSK + SAE). Dacă AP-ul
> difuzează ambele, asocierea e respinsă local (`ASSOC-REJECT status 16`).
> De aceea uplink-ul este un SSID WPA2 clasic.

## 2. Dispozitive Renogy

| Dispozitiv | Nume BLE | MAC | Device ID | Model |
|---|---|---|---|---|
| Încărcător DC-DC + MPPT | `BT-TH-66FAEECA` | `60:98:66:FA:EE:CA` | 96 | **RBC50D1S-G1** |
| Baterie 1 (modul BT-2) | `BT-TH-A583BDF3` | `CC:45:A5:83:BD:F3` | 48 | LiFePO4 cu BT-2 |
| Baterie 2 (BLE integrat) | `RNGPRO12BATT22000714` | `4C:E1:74:52:1C:0E` | 255 | **RBT12100LFP-BT** (100 Ah, self-heating) |

Modelul încărcătorului e citit din registrul 12, modelul bateriei din registrul
5122 (`RBT12100LFP-BT 01 16`).

Serviciu GATT folosit (BT-2): write `0000ffd1-…`, notify `0000fff1-…`.

## 3. Cablare relevantă

```text
panouri PV ──┐
             ├── RBC50D1S-G1 ── baterii 12 V ── consumatori
alternator ──┘        │
   (bornă +)          └── BT-2 (BLE)
```

Intrarea „alternator" a încărcătorului este legată la **bateria de starter**.
Consecință practică:

| Tensiune la intrarea alternator | Interpretare |
|---|---|
| 12.6–12.9 V, 0 A | motor oprit, se citește bateria de starter |
| 13.0–13.6 V | tranziție (motor tocmai pornit) |
| > 13.6 V, curent > 0 | motor pornit, alternatorul încarcă |

## 4. Hărți de registre

Modbus RTU peste BLE, funcția `0x03` (read holding registers), big-endian.

### 4.1 Încărcător DC-DC — registrul 256, 30 words

| Word | Câmp | Scară |
|---|---|---|
| 0 | SOC raportat | % |
| 1 | tensiune baterie | ×0.1 V |
| 2 | curent total ieșire | ×0.01 A |
| 3 | temp controller (byte înalt) / temp baterie (byte jos) | °C |
| 4 | tensiune alternator | ×0.1 V |
| 5 | curent alternator | ×0.01 A |
| 6 | putere alternator | W |
| 7 | tensiune PV | ×0.1 V |
| 8 | curent PV | ×0.01 A |
| 9 | putere PV | W |
| 11 | tensiune minimă azi | ×0.1 V |
| 12 | tensiune maximă azi | ×0.1 V |
| 13 | curent maxim azi | ×0.01 A |
| 15 | putere maximă de încărcare azi | W |
| 17 | Ah încărcați azi | Ah |
| 19 | energie produsă azi | Wh |
| 21 | zile de funcționare | zile |
| 22 | supra-descărcări | număr |
| 23 | încărcări complete | număr |
| 24–25 | Ah acumulați total | Ah (32 bit) |
| 28–29 | energie totală | Wh (32 bit) |

Registrul 288 (3 words) este documentat în alte librării ca „stare", dar pe acest
firmware întoarce zerouri — nu este folosit.

### 4.2 Baterie (BT-2 și nativă) — trei citiri în aceeași sesiune

| Registru | Words | Conținut |
|---|---|---|
| 5000 | 17 | număr celule + tensiuni celule (×0.1 V) |
| 5017 | 17 | număr senzori + temperaturi (×0.1 °C, signed) |
| 5042 | 6 | curent (×0.01 A, signed), tensiune (×0.1 V), Ah rămași, capacitate Ah |
| 5122 | 8 | model (ASCII) |
| 5223 | 1 | device id |

SOC-ul bateriilor se calculează din BMS: `Ah rămași / capacitate × 100`, nu se
ia din încărcător.

## 5. Cum se deduc sursele de încărcare

Nu există shunt pe instalație, deci:

```text
solar      = pv_current_a        (de la încărcător)
alternator = alternator_current_a (de la încărcător)
baterie net = suma curenților BMS ai bateriilor
priză      = max(0, baterie net − curent ieșit din DC-DC)
consum     = max(0, −baterie net)
```

Cât timp bateria se încarcă, consumul nu poate fi separat de curentul de
încărcare fără shunt — se afișează 0, nu o estimare inventată.

## 6. Limite cunoscute

| Limită | Detaliu |
|---|---|
| Fără shunt | consumul e dedus, nu măsurat, în timpul încărcării |
| Fără ieșire „load" | RBC50D1S-G1 nu are bornă de sarcină, deci nu există câmpuri load |
| Încălzirea bateriei | BMS nu expune flag Modbus; starea e dedusă din temperatură și curent |
| WPA3 | chipsetul Broadcom din Pi Zero W nu acceptă AP-uri WPA2/WPA3 mixed |
| O conexiune BLE | un singur central; aplicația Renogy de pe telefon poate rata ferestrele |
| Bateria 2 | nu se anunță în scanări; conexiunea funcționează doar după un `Disconnect` curat în BlueZ |

## 7. Capturi de referință

Fixture-uri brute din hardware (utilizate în teste):

```text
DC-DC  256: 006400910000090a0080000000000000000000000000008600940064
            0000000d000000030000002a000005980000009c0000e53d00000000000c1014
Bat    5000: 0004 0021 0021 0021 0021
Bat    5017: 0002 0076 0074
Bat    5042: fff4 0085 00018dec 0001a2de
```

Ultimul rând, interpretat: −0.12 A, 13.3 V, 102.726 Ah rămași din 107.23 Ah
(≈ 95.8% SOC).
