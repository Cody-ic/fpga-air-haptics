"""Read-only session observer for local tools; never opens another COM port."""

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import threading
import time


def _json_value(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, bytes):
        return value.decode('ascii', errors='replace')
    if hasattr(value, 'tolist'):
        return value.tolist()
    raise TypeError(type(value).__name__)


class SessionMirror(threading.Thread):
    def __init__(self, session, root=None):
        super().__init__(daemon=True, name='touchsee-local-record')
        self.session = session
        self.events = session.subscribe()
        self.root = Path(root) if root else Path(os.environ.get('LOCALAPPDATA',Path.home()))/'TouchSee'/'live'
        self.directory = self.root/('user-'+str(time.time_ns()))
        self.error = None

    @staticmethod
    def _atomic(path, value):
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=_json_value),encoding='utf-8')
        os.replace(temporary,path)

    def run(self):
        try:
            self.directory.mkdir(parents=True,exist_ok=True)
            self._atomic(self.root/'latest.json', {'directory':str(self.directory),
                         'traffic':str(self.directory/'traffic.jsonl'),
                         'snapshot':str(self.directory/'snapshot.json'),
                         'source':'DEMO' if self.session.is_demo else 'DEVICE',
                         'application':'TouchSee User'})
            with (self.directory/'traffic.jsonl').open('a',encoding='utf-8') as stream:
                while not self.session.closing.is_set() or not self.events.empty():
                    try:
                        event = self.events.get(timeout=.15)
                    except queue.Empty:
                        continue
                    timestamp = datetime.now(timezone.utc).isoformat()
                    stream.write(json.dumps(dict(event, recorded_at=timestamp),ensure_ascii=False,default=_json_value)+'\n')
                    stream.flush()
                    if event['kind']=='state':
                        state = event['state']
                        self._atomic(self.directory/'snapshot.json',dict(
                            recorded_at=timestamp, simulated=state.simulated, boot=state.boot,
                            revision=state.revision, sample=state.sample, mode=state.mode,
                            state=state.state, output=state.output, reason=state.reason,
                            focus_mm=state.focus_mm, array=state.array.wire(),
                            config=state.config.wire()))
        except (OSError, ValueError, TypeError) as error:
            self.error = str(error)
        finally:
            self.session.unsubscribe(self.events)
