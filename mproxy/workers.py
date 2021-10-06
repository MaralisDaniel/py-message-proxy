import aiohttp
import asyncio
import logging
import random

from .exceptions import WorkerAwaitError, WorkerExecutionError
from .model import Message

CLIENT_TOTAL_TIMEOUT = 30
DEFAULT_LOGGER_NAME = 'm-proxy.worker'


# Interface for any custom worker
class BaseWorker:
    async def operate(self, message: Message) -> None:
        raise NotImplementedError()


class BaseHTTPWorker(BaseWorker):
    def __init__(self, url: str, method: str) -> None:
        self._url = url
        self._method = method
        self._timeout = aiohttp.ClientTimeout(CLIENT_TOTAL_TIMEOUT)

    async def operate(self, message: Message) -> None:
        raise NotImplementedError()

    async def execute_query(self, data: dict = None) -> dict:
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.request(self._method, self._url, data=data) as response:
                result = {'status': response.status, 'retry-after': response.headers.get('Retry-After')}

                if response.content_type == 'application/json':
                    result['data'] = await response.json()
                else:
                    result['data'] = response.text()

                return result


# Default workers
class Telegram(BaseHTTPWorker):
    def __init__(
            self,
            channel: str,
            *,
            url: str,
            chat_id: int,
            bot_id: int,
            no_notify: bool = False,
            parse_mode: str = None,
            logger: logging.Logger = None,
    ) -> None:
        super().__init__(f"{url.rstrip('/')}/bot{bot_id}/sendMessage", 'POST')

        self.channel = channel

        self._data = {
            'chat_id': chat_id,
            'disable_notification': no_notify,
        }

        if len(parse_mode) > 0:
            self._data['parse_mode'] = parse_mode

        self._log = logger or logging.getLogger(DEFAULT_LOGGER_NAME)

    async def operate(self, message: Message) -> None:
        self._log.debug('Perform request')
        response = await self.execute_query({'text': message.text, **self._data})

        if response['data'].get('ok', False):
            self._log.info(
                    'Channel %s accepted the message, its id: %d',
                    self.channel,
                    response['data']['result']['message_id'],
            )
        else:
            reason = response.get('data', {}).get('description')

            self._log.warning(
                    'Channel %s declined the message, status: %d, reason: %s',
                    self.channel,
                    response['status'],
                    reason,
            )

            if response['status'] == 503:
                retry_after = response.get('data', {}).get('retry_after') or response['retry-after']

                raise WorkerAwaitError(503, reason, retry_after)

            raise WorkerExecutionError(response['status'], reason)


class Stub(BaseWorker):
    def __init__(
            self,
            channel: str,
            *,
            min_delay: int = 1,
            max_delay: int = 5,
            delay_chance: int = 20,
            error_chance: int = 5,
            logger: logging.Logger = None,
    ) -> None:
        self.channel = channel

        self._min_delay = min_delay
        self._max_delay = max_delay
        self._error_chance = error_chance
        self._delay_chance = delay_chance

        self._log = logger or logging.getLogger(DEFAULT_LOGGER_NAME)

    async def operate(self, message: Message) -> None:
        delay = random.randint(self._min_delay, self._max_delay)
        coin = random.randint(0, 100)

        self._log.debug('Sleeping for %d', delay)

        await asyncio.sleep(delay)

        if coin <= self._error_chance:
            self._log.info(f'After {delay} seconds "{message}" was rejected by {self.channel}')

            raise WorkerExecutionError(400, 'Emulate error in request processing')
        elif coin <= self._delay_chance:
            self._log.info(f'After {delay} seconds "{message}" take too long to accept by {self.channel}')

            raise WorkerAwaitError(503, 'Emulate error in request processing')

        self._log.info(f'After {delay} seconds "{message}" was sent to {self.channel}')


__all__ = ['Stub', 'Telegram', 'BaseWorker', 'BaseHTTPWorker']