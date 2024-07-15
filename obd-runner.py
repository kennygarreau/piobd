from obd import OBDCommand, Unit
from obd.protocols import ECU
import obd, logging
import requests
import time, threading
from pythonjsonlogger import jsonlogger
from opentelemetry import trace, metrics
from opentelemetry.metrics import Observation #, CallbackOptions
from opentelemetry.trace import Status, StatusCode
from opentelemetry.sdk.resources import SERVICE_NAME, DEPLOYMENT_ENVIRONMENT, Resource
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, ConsoleMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.instrumentation.requests import RequestsInstrumentor

class GlobalAttributeSpanProcessor(SimpleSpanProcessor):
    def on_start(self, span, parent_context):
        span.set_attribute("vehicle.vin", vin)

# dummy URL
url = "https://myvehicle.att.com/"

# Setup logging
logger = logging.getLogger('obd_logger')
logger.setLevel(logging.INFO)
logFile = '/var/log/obd.log'
file_handler = logging.FileHandler(logFile)
#logHandler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter(
    fmt='%(asctime)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S'
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# setup tracing
#processor = BatchSpanProcessor(ConsoleSpanExporter())
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint="http://localhost:4318/v1/traces"))

resource = Resource(attributes={SERVICE_NAME: "pi-obd", DEPLOYMENT_ENVIRONMENT: "pi-obd.dev"})
span_provider = TracerProvider(resource=resource)
span_provider.add_span_processor(processor)
trace.set_tracer_provider(span_provider)
tracer = trace.get_tracer("obd.tracer")

# setup metrics
console_metric_exporter = ConsoleMetricExporter()
otlp_metric_exporter = OTLPMetricExporter(endpoint="http://localhost:4318/v1/metrics")
metric_reader = PeriodicExportingMetricReader(otlp_metric_exporter, export_interval_millis=15000)
#metric_readers = [
#    PeriodicExportingMetricReader(console_metric_exporter, export_interval_millis=60000),
#    PeriodicExportingMetricReader(otlp_metric_exporter, export_interval_millis=60000)
#]

metrics.set_meter_provider(MeterProvider(
    resource=resource,
    metric_readers=[metric_reader]
))
meter = metrics.get_meter("obd.meter")

responseTime = 0.0
fuelLevel = 0.0
vehMileage = 0.0
prefix = "vehicle"
required_commands = [obd.commands.VIN, obd.commands.FUEL_LEVEL, obd.commands.OBD_COMPLIANCE, obd.commands.HYBRID_BATTERY_REMAINING]

fuel_level_mutex = threading.Lock()
http_response_mutex = threading.Lock()
mileage_mutex = threading.Lock()

# create observable callbacks for each vehicle metric to be instrumented
def register_callbacks():
    def response_time_observable_callback(options):
        with http_response_mutex:
            return [Observation(value=responseTime, attributes=attributes)]

    def fuel_level_observable_callback(options):
        with fuel_level_mutex:
            return [Observation(value=fuelLevel, attributes=attributes)]

    def mileage_observable_callback(options):
        with mileage_mutex:
            return [Observation(value=vehMileage, attributes=attributes)]

    responseTimeGauge = meter.create_observable_gauge(
            callbacks=[response_time_observable_callback],
            name="vehicle.wifi.http.response",
            description="in-vehicle wifi http response time from myvehicle.att.com",
            unit="ms")

    fuelLevelGauge = meter.create_observable_gauge(
            callbacks=[fuel_level_observable_callback],
            name="vehicle.fuel.level",
            description="vehicle fuel level",
            unit="percent")

    mileageGauge = meter.create_observable_gauge(
            callbacks=[mileage_observable_callback],
            name="vehicle.mileage",
            description="vehicle mileage",
            unit="miles")

def connect_async():
    '''
    Establish async OBD connectivity to the vehicle on the USB port.
    TODO: set a variable for the obd.Async() connection
    TODO: move the OBDCommand setter for Mileage to its own location in the code?
    '''
    async_connection = obd.Async("/dev/ttyUSB0")
    c = OBDCommand("MILEAGE", \
               "Vehicle Mileage", \
               b"01A6", \
               6, \
               mileage_calc, \
               ECU.ENGINE, \
               True)
    async_connection.supported_commands.add(c)
    
    register_callbacks()

    async_connection.watch(obd.commands.FUEL_LEVEL, callback=fuel_level_callback)
    logger.info("Watching FUEL_LEVEL pid")
    async_connection.watch(mileage_cmd, callback=mileage_callback)
    logger.info("Watching MILEAGE pid")
    async_connection.start()
    logger.info("Starting async connection...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping async connection...")
        async_connection.stop()

def vin_decode(vin):
    '''
    Decode the VIN via an API call to NHTSA. Manufacturers may not require this or have their own decode method, locally
    or via API.
    '''
    baseurl = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVin/"
    formatter = "?format=json"

    url = baseurl + vin + formatter

    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()

        vehicle_details = {
        "Model Year": None,
        "Make": None,
        "Model": None,
        "Series": None,
        "Manufacturer Name": None,
        "Trim": None,
        "Vehicle Type": None }

        for item in data.get('Results', []):
            variable_name = item.get('Variable')
            if variable_name in vehicle_details:
                vehicle_details[variable_name] = item.get('Value')
        
        for key, value in vehicle_details.items():
            print(f'{key}: {value}')

        return vehicle_details
    else:
        print("VIN API Error")
        return None

def mileage_calc(messages):
    '''
    Vehicles manufactured from 2019 are required by CARB to report their VIN via OBD. This function will decode the bytecode response.
    '''
    d = messages[0].data
    d = d[2:]
    if len(messages) > 0 and len(messages[0].data) >= 4:
        byte_A = d[0]
        byte_B = d[1]
        byte_C = d[2]
        byte_D = d[3]
        distance = (byte_A * 2**24) + (byte_B * 2**16) + (byte_C * 2**8) + byte_D
        # convert km to miles
        mileage = (distance / 10.0) * 0.621371
        return round(mileage, 2)
    return None

def get_dtcs():
    '''
    This will query OBD for the list of DTC codes stored in the ECU. If there are none, it will return a value of 0,
    to be stored as dtc_count.
    '''
    with tracer.start_as_current_span("get-dtc") as span:
        try:
            response = connection.query(obd.commands.GET_DTC)
            if response.value:
                dtcs = response.value
                for dtc in dtcs:
                    print(f"Code: {dtc.code}, Description: {dtc.description}")
            else:
                span.set_attribute("vehicle.dtc.count", 0)
                return 0
        except Exception as e:
            logger.error(f"Failed to get DTCs: {e}")

def get_reading(block):
    '''
    Obtain a reading for an individual block / PID from OBD. This is not used by the async runner.
    '''
    with tracer.start_as_current_span(f"get-reading-{block}") as span:
        try:
            if hasattr(obd.commands, block):
                cmd = getattr(obd.commands, block)
                response = connection.query(cmd)                
                if response and response.value:
                    blockname = block.lower().replace("_", ".")
                    if isinstance(response.value, bytearray):
                        decoded_string = response.value.decode('utf-8')
                        #print(f"{blockname}: ", decoded_string)
                        span.set_attribute(f"{prefix}.{blockname}", decoded_string)
                        return decoded_string
                    else:
                        #print(f"{blockname}: ", response.value.magnitude)
                        span.set_attribute(f"{prefix}.{blockname}", response.value.magnitude)
                else:
                    logger.warning(f"Failed to get a successful response for command {block}.")
                    span.set_status(Status(StatusCode.ERROR))
                    print("Failed to get a successful response.")
            else:
                logger.warning(f"Command '{block}' is not recognized.")
                print(f"Command '{block}' is not recognized.")
        except Exception as e:
            logger.error(f"Failed to get reading for {block}: {e}")

def fuel_level_callback(fuel_level):
    '''
    Dedicated callback for tracking vehicle fuel level; instruments vehicle.fuel.level
    '''
    global fuelLevel
    with tracer.start_as_current_span("fuelLevel.callback") as span:
        current_fuel_level = fuel_level.value.magnitude
        print(f"Fuel Level: {current_fuel_level}")
        span.set_attribute("vehicle.fuel.level", current_fuel_level)
        with fuel_level_mutex:
            fuelLevel = current_fuel_level
        #web_callback()
        time.sleep(60)

def mileage_callback(mileage):
    '''
    Dedicated callback for tracking vehicle mileage; instruments vehicle.mileage
    '''
    global vehMileage
    with tracer.start_as_current_span("mileage.callback") as span:
        current_mileage = mileage.value
        print(f"Vehicle Mileage: {current_mileage}")
        span.set_attribute("vehicle.odometer", current_mileage)
        with mileage_mutex:
            vehMileage = current_mileage

def web_callback():
    '''
    Simple web request and response that generates a span and produces an instrumented response time metric
    '''
    global responseTime
    RequestsInstrumentor().instrument()
    #with tracer.start_as_current_span("att-callback") as span:
    response = requests.get(url)
    current_responseTime = response.elapsed.total_seconds() * 1000
    #span.set_attribute("http.elapsed", current_responseTime)
    with http_response_mutex:
        responseTime = current_responseTime

def get_isp():
    '''
    Obtain the ISP provider directly via a web call.
    '''
    url = 'https://ipinfo.io/json'
    try:
        response = requests.get(url)        
        response.raise_for_status()
        
        data = response.json()
        loc_string = data.get('loc', 'Location information not available')
        coordinates = loc_string.split(',')
        latitude = float(coordinates[0])
        longitude = float(coordinates[1])
        
        isp = data.get('org', 'ISP information not available')
        isp_name = ' '.join(isp.split()[1:]) if isp.startswith("AS") else isp

        return isp_name, loc_string
    
    except requests.RequestException as e:
        return f"An error occurred: {e}"

def add_custom_command(name, description, pid, bytes, decoder, ecu):
    cmd = OBDCommand(name, description, pid, bytes, decoder, ecu, True)
    connection.supported_commands.add(cmd)
    return cmd

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    with tracer.start_as_current_span("obd2.init") as span:
        logger.info("Initializing OBD connection...")
        connection = obd.OBD("/dev/ttyUSB0")

        if connection.is_connected():
            logger.info("Successfully connected to OBD-II adapter!")

            mileage_cmd = add_custom_command("MILEAGE", "Vehicle Mileage", b"01A6", 6, mileage_calc, ECU.ENGINE)
            required_commands.append(mileage_cmd)

            supported_commands = connection.supported_commands
            print("Checking supported commands...")
            for cmd in required_commands:
                if cmd in supported_commands:
                    print(f"{cmd.name}: Supported")
                else:
                    print(f"{cmd.name}: UNSUPPORTED")

            # when the runner starts, we should always obtain the VIN and the DTC count
            vin = get_reading("VIN")

            # for some reason this doesn't read properly on my RAM (omits the leading 1) 
            # works on Beth's Prius - need to dig into the OBDCommand response
            if (len(vin) == 16):
                vin = "1" + vin
            logger.info(f"VIN: {vin}")
            # a simple VIN decode will give us details about the vehicle
            veh_details = vin_decode(vin)
            dtc_count = get_dtcs()
            print(f"DTC Codes Registered: {dtc_count}")
            logger.info(f"DTC Codes Registered: {dtc_count}")

            # debug testing
            #vin = "insertyour17digitvin"

            # obtain ISP details for filtering
            isp, latlong = get_isp()
            
            attributes = {
                    "vehicle.vin": vin,
                    "vehicle.make": veh_details["Make"],
                    "vehicle.model": veh_details["Model"],
                    "vehicle.year": veh_details["Model Year"],
                    "vehicle.series": veh_details["Series"],
                    "vehicle.isp": isp,
                    "vehicle.dtc.count": dtc_count
                    }

            # Start the main loop
            print(f"Gathering supported telemetry for {veh_details['Model Year']} {veh_details['Make']} {veh_details['Model']}")
            logger.info(f"Gathering supported telemetry for {veh_details['Model Year']} {veh_details['Make']} {veh_details['Model']}")
            logger.info(f"Current vehicle position: {latlong}")
            connect_async()
        else:
            print("Failed to connect to OBD-II adapter, exiting")
            span.set_status(Status(StatusCode.ERROR, str("Unable to connect to vehicle")))
            logger.error("Unable to connect to vehicle")
