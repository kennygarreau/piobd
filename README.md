## PiOBD - an OpenTelemetry-enabled in-vehicle monitoring system

<p>Have you ever wanted to...
</p>

1. Monitor your vehicle?
2. Read and clear DTC codes?
3. Not have to use a proprietary app on your phone or laptop to interface with your vehicle?
4. Measure the performance of your in-vehicle WiFi?
5. Use OpenTelemetry to capture all of these signals?

<p>Have a Raspberry Pi and an OBD2 cable/BLE adapter kicking around? Then PiOBD is for you. This uses
https://github.com/brendan-w/python-OBD/tree/master for OBD connectivity functions, and the Python
implementation of OpenTelemetry.</p>

Here's what you (physically) need:
1. Raspberry Pi (1GB+) with MicroSD card
2. ELM-327/OBD-2 Adapter (this was tested using USB)

### Getting Started

1. Image your Raspberry Pi using Ubuntu 20.04 (if you do NOT want to use ThousandEyes, you can use a newer ver)
2. Install the OpenTelemetry collector (eg., https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v0.104.0/otelcol-contrib_0.104.0_linux_arm64.deb)
3. Configure the collector to send to your configured backend (I work for Cisco/Splunk, shameless plug)
4. Be sure Python3 is installed on your Raspberry Pi
5. Install required libs:<br>
pip install obd opentelemetry-api opentelemetry-instrumentation opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc opentelemetry-exporter-otlp-proto-http
6. (optional) Create an account at https://www.thousandeyes.com/ for monitoring your in-vehicle WiFi
7. (optional) Deploy the ThousandEyes Enterprise Agent to your RPI (tip: use the Linux install, not the Appliance)

### Vehicle Support
<p>Some vehicles will support the commands you want to issue and some will not. For example, I am able to obtain the Fuel Level on my 2021 RAM,
but I am not able to do the same for my wife's 2017 Toyota Prius. The longer the command has been an OBD pid, the more likely it is to work. You
will likely be able to fetch DTC codes from any MY vehicle that supports the OBD protocol, but newer pids such as Hybrid Battery Pack life may be
unavailable.</p>

### Starting the Service

<p>By default the software should be placed in /opt/piobd. If you chose to install this as a service using systemd, you should be able to:<br>
edit the user and group, and install path (if you modified from /opt/piobd)<br>
cp obd-runner.service /etc/systemd/system<br>
sudo systemctl daemon-reload<br>
sudo systemctl enable obd-runner.service<br>
sudo systemctl start obd-runner<br>
sudo systemctl status obd-runner<br>
</p>
<p>At this point, the vehicle will begin reporting its telemetry data to your locally running collector, which in turn should publish to the backend.
Of note here, you can modify how much "offline" telemetry the collector gathers, which may be very helpful in scenarios where either the in-vehicle
wifi is unavailable, or you do not have in-vehicle wifi. Using a hotspot (eg., via your phone) is perfectly acceptable.</p>

### Getting Help

<p>Please create an issue if you are having problems with the code itself. If you work for Cisco or Splunk, please reach out to me directly
on Webex. Thanks!</p>
