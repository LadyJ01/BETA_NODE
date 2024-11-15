import asyncio
import aiohttp
import time
import uuid
from loguru import logger
from colorama import Fore, Style, init
import sys

# Initialize colorama
init(autoreset=True)

# Customize loguru to use color for different log levels
logger.remove()
logger.add(sys.stdout, format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{message}</level>", colorize=True)
logger.level("INFO", color=f"{Fore.GREEN}")
logger.level("DEBUG", color=f"{Fore.CYAN}")
logger.level("WARNING", color=f"{Fore.YELLOW}")
logger.level("ERROR", color=f"{Fore.RED}")
logger.level("CRITICAL", color=f"{Style.BRIGHT}{Fore.RED}")

# Global dictionary to track the authentication status of proxies
proxy_auth_status = {}

# Constants
MAX_CONCURRENT_TASKS = 100  # Adjust as per system capability
PING_INTERVAL = 60
RETRIES = 120
TOKEN_FILE = 'np_tokens.txt'
CONNECTION_TIMEOUT = 15  # Timeout in seconds

# Connection states
CONNECTION_STATES = {
    "CONNECTED": 1,
    "DISCONNECTED": 2,
    "NONE_CONNECTION": 3
}

# Initialize global variables
status_connect = CONNECTION_STATES["NONE_CONNECTION"]
browser_id = None
account_info = {}
last_ping_time = {}

# Semaphore to limit the number of concurrent tasks
semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

def uuidv4():
    return str(uuid.uuid4())

def valid_resp(resp):
    if not resp or "code" not in resp or resp["code"] < 0:
        raise ValueError("Invalid response")
    return resp

async def render_profile_info(proxy, token):
    global browser_id, account_info, proxy_auth_status

    try:
        np_session_info = load_session_info(proxy)
        
        if not proxy_auth_status.get(proxy):
            browser_id = uuidv4()
            response = await call_api(DOMAIN_API["SESSION"], {}, proxy, token)
            if response is None:
                return
            valid_resp(response)
            account_info = response["data"]
            
            if account_info.get("uid"):
                proxy_auth_status[proxy] = True
                save_session_info(proxy, account_info)
                logger.info(f"Authentication successful for proxy {proxy} account: {account_info}")
            else:
                handle_logout(proxy)
                return
        
        if proxy_auth_status.get(proxy):
            await start_ping(proxy, token)

    except Exception as e:
        logger.error(f"Error in render_profile_info for proxy {proxy}: {e}")

# Retry logic with exponential backoff
async def call_api(url, data, proxy, token, max_retries=5, timeout=CONNECTION_TIMEOUT):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://app.nodepay.ai",
    }

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=True)) as session:
        for attempt in range(max_retries):
            try:
                async with session.post(url, json=data, headers=headers, proxy=proxy, timeout=timeout) as response:
                    response.raise_for_status()
                    resp_json = await response.json()
                    return valid_resp(resp_json)
            except aiohttp.ClientResponseError as e:
                if e.status == 403:
                    logger.error(f"403 Error: Forbidden for proxy {proxy}")
                    return None
            except aiohttp.ClientConnectionError as e:
                logger.warning(f"Connection error for proxy {proxy}, retrying...")
            except asyncio.TimeoutError as e:
                logger.warning(f"Timeout error for proxy {proxy}, retrying...")
            except Exception as e:
                logger.error(f"Error occurred for proxy {proxy}: {e}")
            
            # Exponential backoff
            await asyncio.sleep(2 ** attempt)  # Increasing delay between retries

    return None

async def start_ping(proxy, token):
    try:
        while True:
            await ping(proxy, token)
            await asyncio.sleep(PING_INTERVAL)
    except asyncio.CancelledError:
        logger.info(f"{Fore.YELLOW}Ping task for proxy {proxy} was cancelled")
    except Exception as e:
        logger.error(f"{Fore.RED}Error in start_ping for proxy {proxy}: {e}")

async def ping(proxy, token):
    global last_ping_time, RETRIES, status_connect

    current_time = time.time()
    if proxy in last_ping_time and (current_time - last_ping_time[proxy]) < PING_INTERVAL:
        return

    last_ping_time[proxy] = current_time
    ping_urls = DOMAIN_API["PING"]

    for url in ping_urls:
        try:
            data = {
                "id": account_info.get("uid"),
                "browser_id": browser_id,
                "timestamp": int(time.time()),
                "version": '2.2.7'
            }
            logger.warning(f"Starting ping task for proxy {proxy} Data: {data}")
            response = await call_api(url, data, proxy, token)
            if response and response["code"] == 0:
                logger.info(f"{Fore.CYAN}Ping successful via proxy {proxy} - {response}")
                RETRIES = 0
                status_connect = CONNECTION_STATES["CONNECTED"]
                return 
            else:
                logger.error(f"{Fore.RED}Ping failed via proxy {proxy} - {response}")
                handle_ping_fail(proxy, response)
        except Exception as e:
            logger.error(f"{Fore.RED}Ping error via proxy {proxy}: {e}")

    handle_ping_fail(proxy, None)  

def handle_ping_fail(proxy, response):
    global RETRIES, status_connect

    RETRIES += 1
    if response and response.get("code") == 403:
        handle_logout(proxy)
    elif RETRIES < 2:
        status_connect = CONNECTION_STATES["DISCONNECTED"]
    else:
        status_connect = CONNECTION_STATES["DISCONNECTED"]

# Rest of the functions remain the same as before
