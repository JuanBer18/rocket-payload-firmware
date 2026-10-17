# HYPOWER payload continuous-mission power budget

Revision: 9 August 2026

## Direct answer

- **ESP32-S3-WROOM-1-N8R2:** budget **100 mA average**, expect roughly **70-130 mA** depending on dashboard traffic and Wi-Fi airtime, and design for the documented **340 mA RF transmit peak**. Firmware alone cannot determine the real average; this value must be measured at the board.
- **SEN65:** the datasheet specifies **80 mA typical / 100 mA maximum average** in measurement mode after the first 60 seconds, with **140 mA typical / 200 mA maximum 2 ms peaks**. It requires **3.15-3.6 V** (3.3 V nominal).
- **Complete payload:** a reasonable continuous-mission planning value is **about 210 mA at 3.3 V (0.69 W)**. Allow **165-300 mA average** until measured and at least **0.75 A transient**. A regulated 3.3 V source rated for **at least 1 A continuous** is the minimum sensible choice; **1.5 A** adds useful margin for the SD card, wiring loss, and cold startup.

## Budget table

| Load | Board rail | Nominal continuous | Upper planning value | Transient peak | Basis |
|---|---:|---:|---:|---:|---|
| ESP32-S3-WROOM-1-N8R2 | +3V3 | 100 mA | 130 mA | 340 mA | Estimate for this firmware; RF peak from Espressif |
| SEN65 | 3V3_SR | 80 mA | 100 mA | 200 mA | Sensirion Table 11 |
| microSD | 3V3_SR | 25 mA | 60 mA | 200 mA | Card-dependent planning allowance |
| LSM6DSO32 | 3V3_SR | 0.55 mA | 0.55 mA | 0.55 mA | ST combo high-performance mode |
| BME280 | 3V3_SR | 1.0 mA | 1.2 mA | 1.2 mA | Conservative allowance for configured normal mode |
| Power LED | +3V3 | 1.3 mA | 2.0 mA | 2.0 mA | D4 with R12 = 1 kohm |
| Regulators, pull-ups and miscellaneous | mixed | 2 mA | 4 mA | 4 mA | Allowance; external-converter loss excluded |
| **Total** | mixed | **209.9 mA** | **297.8 mA** | **747.8 mA** | Worst-case peaks intentionally conservative |

The 210 mA value is the useful design estimate, not a claim that the board will sit at one exact current. Wi-Fi and SD current are bursty. The ESP32 figure changes when a browser is connected, live plots are active, files are downloaded, or RF conditions cause retransmissions.

## Why this represents continuous mission firmware

The code keeps the Wi-Fi access point active from boot, runs the controller continuously, records the LSM6DSO32 at 833 Hz, operates the BME280 in normal mode with x16 pressure oversampling, polls the SEN65 continuously, and writes/flushed binary records to microSD. The optional browser plots change network traffic, but not the underlying sensor logging rates.

## Important board-rail finding

The custom-board schematic does not put every load on the same named rail:

- The ESP32 module is powered from **+3V3**, selected at J10 from either `3V3_USB` or `3V3_SR`.
- The SEN65 connector, LSM6DSO32, BME280, and microSD are wired directly to **3V3_SR**.

Therefore, selecting USB at J10 powers the ESP32 from the USB LDO, but it does **not** by itself create `3V3_SR` for the sensors. The battery/external converter must still provide a correct 3.3 V `3V3_SR` rail. If J10 selects `3V3_SR`, the battery-side 3.3 V converter supplies the whole payload.

## USB and battery implications

If the entire 210 mA load passed through a 5 V-to-3.3 V linear regulator, input current would remain roughly 210 mA and regulator dissipation would be about **0.36 W**: `(5.0 - 3.3) x 0.210`. At the conservative 298 mA upper value it would be about **0.51 W**. On this schematic, however, the sensors are on `3V3_SR`, so actual heat splits according to the J10 selection and the external converter arrangement.

For a 3.7 V battery and a 90%-efficient 3.3 V converter, the nominal 0.69 W payload corresponds to about **208 mA from the battery**. Runtime in hours is approximately `usable battery capacity (mAh) / 208 mA`, then derated for battery temperature, voltage sag, converter cutoff, and capacity rating conditions.

## Verification measurement

Put an ammeter or power analyser in series with each source rail and record at least four cases: boot/startup, continuous mission with no browser, dashboard connected, and live plots/file download. Use a meter with peak capture or an oscilloscope across a small current-shunt resistor; an ordinary multimeter can miss the 2 ms SEN65 pulse and Wi-Fi/SD bursts.

## Sources and confidence

- `sen65 information/PS_DS_SEN6x.pdf`, page 13, Table 11: SEN65 voltage and current.
- `lsm6dso32.pdf`, page 28, Table 4: 0.55 mA combo high-performance current.
- `Schematic PDF_[No Variations] (1).pdf`: module identity, rail routing, J10 selection, SD/sensor rails, and power LED.
- Espressif ESP32-S3 Series Datasheet v2.2, pages 66-68: 340 mA Wi-Fi TX peak, 88-91 mA RX, and CPU/modem-sleep reference values. The 100 mA ESP32 continuous value is an engineering estimate because the datasheet does not characterize this exact MicroPython AP workload.
- Bosch BME280 product data: 3.6 uA typical at 1 Hz. This firmware operates much faster and with high pressure oversampling, so the budget deliberately uses a conservative 1 mA allowance.
- microSD current is not fixed by the SD interface. The listed values are planning allowances and must be replaced by a measurement of the actual card.
