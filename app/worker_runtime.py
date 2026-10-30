"""PROPOSED Linux worker ownership; this module never interprets or runs a job.

Only start() launches. The command factory is trusted, never HTTP input. An exec
bootstrap sets child RLIMIT_CORE without threaded preexec_fn or parent limits.
An optional, already-locked owner_fd remains inherited across exec. Before worker
exec the bootstrap arms Linux parent-death SIGKILL and rechecks the captured parent
PID. This protects the default worker, not arbitrary custom-command descendants:
PDEATHSIG concerns the creating parent thread, is not inherited across fork, and
can be cleared by privileged exec. Trusted wrappers must preserve the lock fd.
Custom commands enable a process-scoped subreaper, retained thereafter.
This is not containment: unobserved session/ancestry escapes can defeat attribution.
Unknown adopted children or incomplete ownership inventories prevent quiescence. Default workers
have no subprocesses. Kernel/I/O stalls have no universal time guarantee; cleanup
uses real monotonic deadlines, independently of the coordinator's logical clock.
"""
import ctypes
import errno
import functools
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid


STREAM_CAP = 64 * 1024
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAFE_ENV = {
    'PATH': '/usr/local/bin:/usr/bin:/bin',
    'LANG': 'C.UTF-8',
    'PYTHONDONTWRITEBYTECODE': '1',
    'PYTHONPATH': str(PROJECT_ROOT),
}
# Exec preserves PID/session and uses exactly the configured worker argv.
_BOOTSTRAP = '''import ctypes,os,resource,signal,sys
parent,owner=int(sys.argv[1]),int(sys.argv[2])
libc=ctypes.CDLL(None,use_errno=True)
if libc.prctl(1,signal.SIGKILL,0,0,0)!=0:
    raise OSError(ctypes.get_errno(),'SET_PDEATHSIG')
if os.getppid()!=parent:
    os._exit(125)
if owner>=0:
    os.set_inheritable(owner,True)
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
os.execve(sys.argv[3],sys.argv[3:],dict(os.environ))
'''


def _stat(pid):
    """Linux stat fields, including immutable process birth tick."""
    with open('/proc/%d/stat' % pid, 'rb', buffering=0) as stream:
        raw = stream.read(4096)
    fields = raw.rpartition(b') ')[2].split()
    if len(fields) < 20:
        raise ValueError('PROC_STAT')
    return {'pid': pid, 'state': fields[0].decode('ascii'),
            'ppid': int(fields[1]), 'pgid': int(fields[2]),
            'session': int(fields[3]), 'start_tick': int(fields[19])}


def _directory(path):
    """Open the absolute directory chain without following any symlink."""
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('JOB_DIRECTORY_PATH')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open('/', flags)
    try:
        for part in path.parts[1:]:
            following = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = following
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('JOB_DIRECTORY_PRIVATE')
        return fd
    except BaseException:
        os.close(fd)
        raise


def _serialized(method):
    @functools.wraps(method)
    def call(self, *args, **kwargs):
        with self._guard:
            return method(self, *args, **kwargs)
    return call


class WorkerProcess:
    """Single-use supervisor; a private lock serializes lifecycle operations.

    Success requires no failure, quiescent cleanup and complete diagnostics.
    Incomplete stop retains ownership/pidfds/pipes; stop() can be retried.
    """

    def __init__(self, job_file: Path, job_dir: Path, worker_command=None, *, owner_fd=None):
        self.job_file = Path(job_file)
        self.job_dir = Path(job_dir)
        self.worker_command = worker_command
        self.owner_fd = owner_fd  # Borrowed descriptor: never close/unlock it here.
        self.diagnostics = {}
        self.failures = []
        self.cleanup = {'quiescent': False, 'started': False, 'exit_code': None,
                        'owned_live': [], 'owned_zombies': [],
                        'unattributed_children': [], 'process_table_complete': False,
                        'signals': [], 'subreaper_enabled': False,
                        'subreaper_changed': False,
                        'ownership_scope': 'verified PID birth ticks, ancestry and sessions'}
        self._process = None
        self._attempted = False
        self._stopping = False
        self._emergency = False
        self._sealed = False
        self._streams = {}
        self._known = {}
        self._sessions = set()
        self._baseline_children = set()
        self._observed = 0
        self._guard = threading.RLock()
        self._wake = threading.Event()
        self._watcher = None
        self._request_reason = None
        self._started_at = None
        self._clock = time.monotonic
        self._deadline = self._real_deadline = None
        self.timed_out = False

    @_serialized
    def arm_deadline(self, seconds, clock=None):
        if (not isinstance(seconds, (int, float)) or not math.isfinite(seconds)
                or not 0 < seconds <= 180 or self._process is None):
            raise ValueError('DEADLINE_BOUND_OR_STATE')
        if self._sealed:
            return self
        clock = time.monotonic if clock is None else clock
        now = clock()  # Trusted, nonblocking test clock; never a coordinator lock.
        if not math.isfinite(now):
            raise ValueError('DEADLINE_CLOCK')
        reserve = min(1.5, seconds / 2)
        completion = min(self._started_at + 180, time.monotonic() + seconds)
        self._clock, self._deadline = clock, now + seconds - reserve
        self._real_deadline = completion - reserve
        self.cleanup.update(completion_deadline=now + seconds, deadline_trigger=self._deadline,
                            real_completion_deadline=completion, cleanup_reserve_seconds=reserve)
        if self._watcher is None:
            self._watcher = threading.Thread(target=self._watch, name='worker-watchdog', daemon=True)
            self._watcher.start()
        self.wake()
        return self

    def request_stop(self, reason):
        self._request_reason = str(reason)[:128] or 'STOP_REQUESTED'
        self.cleanup['stop_reason'] = self._request_reason
        self.wake()

    def wake(self):
        self._wake.set()

    def _watch(self):
        while not self._sealed:
            try:
                expired = (time.monotonic() >= self._real_deadline
                           or self._clock() >= self._deadline)
                reason = self._request_reason
                if expired or reason is not None:
                    with self._guard:
                        self.timed_out = expired
                        self.cleanup.update(timeout=expired, stop_reason=reason or 'WORKER_TIMEOUT')
                        self._fail('WORKER_TIMEOUT' if expired else 'STOP_REQUESTED')
                        self.stop(grace=0.0)
                    if not self.cleanup['quiescent']:
                        return  # Ownership remains retained for an explicit retry.
                elif self.poll() is not None:
                    self.finish()
                    return
            except Exception:
                with self._guard:
                    self._fail('WATCHDOG_FAILED')
                    try:
                        self.stop(grace=0.0)
                    except Exception:
                        self._fail('WATCHDOG_CLEANUP_FAILED')
                return
            self._wake.wait(0.02)
            self._wake.clear()

    @property
    def pid(self):
        return None if self._process is None else self._process.pid

    @property
    def failure(self):
        return self.failures[0] if self.failures else None

    @property
    def failed(self):
        return bool(self.failures)

    def _fail(self, code):
        if code not in self.failures:
            self.failures.append(code)
        self.cleanup['failures'] = list(self.failures)
        self.cleanup['failed'] = True

    def _table(self):
        # Inventory only the ownership domain. The isolated session can acquire
        # members through fork lineage; adopted orphans appear among coordinator
        # thread children. Unattributed adoptees remain failures, never targets.
        # Unrelated host process count and scheduler/GIL wait are not work budgets.
        table = {}; complete = True; visited = set(); thread_count = 0
        wall_start = time.monotonic(); cpu_start = time.thread_time()
        budget = cpu_start + 0.02
        failure = None

        def bound():
            if time.thread_time() >= budget:
                raise RuntimeError('OWNERSHIP_INVENTORY_CPU_BOUND')
            if len(table) > 65536 or len(visited) > 4096:
                raise RuntimeError('OWNERSHIP_INVENTORY_ENTRY_BOUND')

        def row(pid):
            bound()
            try:
                value = _stat(pid)
            except (FileNotFoundError, ProcessLookupError):
                return None
            table[pid] = value
            return value

        def children(info):
            nonlocal thread_count
            out = set()
            try:
                with os.scandir('/proc/%d/task' % info['pid']) as tasks:
                    for task in tasks:
                        bound()
                        if not task.name.isdigit():
                            continue
                        thread_count += 1
                        if thread_count > 4096:
                            raise RuntimeError('OWNERSHIP_INVENTORY_THREAD_BOUND')
                        try:
                            with open(task.path + '/children', 'rb', buffering=0) as stream:
                                raw = stream.read(65537)
                        except (FileNotFoundError, ProcessLookupError):
                            continue
                        if len(raw) > 65536:
                            raise RuntimeError('OWNERSHIP_INVENTORY_CHILD_BOUND')
                        values = raw.split()
                        if any(not value.isdigit() for value in values):
                            raise ValueError('CHILDREN_FORMAT')
                        out.update(int(value) for value in values)
                        if len(out) > 4096:
                            raise RuntimeError('OWNERSHIP_INVENTORY_CHILD_BOUND')
                after = _stat(info['pid'])
                if after['start_tick'] != info['start_tick']:
                    raise RuntimeError('OWNERSHIP_INVENTORY_IDENTITY_CHANGED')
            except (FileNotFoundError, ProcessLookupError):
                return set()
            return out

        try:
            parent = _stat(os.getpid())
            # All coordinator threads matter: Popen is launched by the supervisor
            # thread, while an adopted descendant may belong to another thread.
            for pid in children(parent):
                row(pid)
            pending = [(pid, item['info']['start_tick']) for pid, item in self._known.items()]
            while pending:
                pid, born = pending.pop()
                if (pid, born) in visited:
                    continue
                visited.add((pid, born)); bound()
                info = row(pid)
                if info is None or info['start_tick'] != born:
                    continue  # A recycled PID never anchors ancestry or signals.
                for child in children(info):
                    value = row(child)
                    if value is not None:
                        pending.append((child, value['start_tick']))
            # Catch adoption during the lineage walk before reaping any anchor.
            # A still-live row prevents quiescence; a zombie has already reparented
            # its children. An unseen adoptee is retained as unknown by _scan().
            for pid in children(parent):
                row(pid)
        except RuntimeError as exc:
            failure = str(exc)
            self._fail(failure)
            complete = False
        except (OSError, ValueError, UnicodeError):
            failure = 'PROCESS_SCAN_UNREADABLE'
            self._fail(failure)
            complete = False
        facts = {'scope': 'coordinator thread children and verified owned lineage',
                 'process_rows': len(table), 'lineage_nodes': len(visited),
                 'thread_directories': thread_count, 'complete': complete,
                 'wall_seconds': time.monotonic() - wall_start,
                 'thread_cpu_seconds': time.thread_time() - cpu_start,
                 'cpu_budget_seconds': 0.02, 'failure': failure}
        self.cleanup['last_inventory'] = facts
        self.cleanup['process_table_scope'] = facts['scope']
        if failure:
            self.cleanup.setdefault('inventory_failure', facts)
        return table, complete

    def _subreaper(self):
        libc = ctypes.CDLL(None, use_errno=True)
        old = ctypes.c_int()
        if libc.prctl(37, ctypes.byref(old), 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), 'GET_CHILD_SUBREAPER')
        if not old.value and libc.prctl(36, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), 'SET_CHILD_SUBREAPER')
        self.cleanup.update(subreaper_enabled=True, subreaper_changed=not bool(old.value),
                            subreaper_scope='coordinator process; remains enabled')

    def _remember(self, info):
        pid, born = info['pid'], info['start_tick']
        prior = self._known.get(pid)
        if prior is not None and prior['info']['start_tick'] == born:
            prior['info'] = info
            return True
        if pid == os.getpid() or len(self._known) >= 4096:
            self._fail('OWNERSHIP_BOUND')
            return False
        fd = None
        try:
            fd = os.pidfd_open(pid, 0)
            current = _stat(pid)
            if current['start_tick'] != born:
                os.close(fd)
                return False
        except (FileNotFoundError, ProcessLookupError):
            if fd is not None:
                os.close(fd)
            return False
        except (OSError, ValueError, UnicodeError):
            if fd is not None:
                os.close(fd)
            self._fail('PIDFD_UNAVAILABLE')
            return False
        if prior is not None:
            os.close(prior['fd'])
        self._known[pid] = {'fd': fd, 'info': current, 'sent': set()}
        self._observed += 1
        return True

    @_serialized
    def start(self):
        if self._attempted or self._sealed:
            raise RuntimeError('WORKER_START_ONCE')
        self._attempted = True
        directory = None
        try:
            if (not sys.platform.startswith('linux') or not hasattr(os, 'pidfd_open')
                    or not hasattr(signal, 'pidfd_send_signal') or not hasattr(os, 'P_PIDFD')):
                raise RuntimeError('LINUX_PIDFD_REQUIRED')
            probe = os.pidfd_open(os.getpid(), 0)
            os.close(probe)
            inherited = ()
            if self.owner_fd is not None:
                if type(self.owner_fd) is not int or self.owner_fd < 3:
                    raise ValueError('OWNER_LOCK_DESCRIPTOR')
                info = os.fstat(self.owner_fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != os.getuid() or info.st_mode & 0o077):
                    raise ValueError('OWNER_LOCK_PRIVATE')
                inherited = (self.owner_fd,)
            if (not self.job_file.is_absolute() or '..' in self.job_file.parts
                    or self.job_file.parent != self.job_dir):
                raise ValueError('JOB_FILE_PATH')
            directory = _directory(self.job_dir)
            fd = os.open(self.job_file.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=directory)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != os.getuid() or info.st_mode & 0o077):
                    raise ValueError('JOB_FILE_PRIVATE')
            finally:
                os.close(fd)
            if self.worker_command is None:
                argv = [sys.executable, '-B', '-m', 'app.coordinator_worker',
                        '--job-file', str(self.job_file)]
            else:
                if not callable(self.worker_command):
                    raise ValueError('TRUSTED_COMMAND_FACTORY_REQUIRED')
                argv = self.worker_command(self.job_file)
            if (not isinstance(argv, (list, tuple)) or not 1 <= len(argv) <= 256
                    or any(not isinstance(arg, str) or '\x00' in arg for arg in argv)
                    or not Path(argv[0]).is_absolute()
                    or sum(len(arg) for arg in argv) > 131072):
                raise ValueError('TRUSTED_WORKER_ARGV')
            table, complete = self._table()
            if not complete:
                raise RuntimeError('INITIAL_PROCESS_SCAN')
            self._baseline_children = {(pid, row['start_tick']) for pid, row in table.items()
                                       if row['ppid'] == os.getpid()}
            if self.worker_command is not None:
                self._subreaper()
            token = uuid.uuid4().hex
            for label in ('stdout', 'stderr'):
                name = 'worker-' + token + '.' + label + '.bin'
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                             | os.O_CLOEXEC, 0o600, dir_fd=directory)
                self._streams[label] = {'fd': fd, 'pipe': None, 'bad': False, 'eof': False}
                self.diagnostics[label] = {'path': str(self.job_dir / name),
                                          'observed_bytes': 0, 'retained_bytes': 0,
                                          'complete': False}
            parent_pid = os.getpid()
            self._process = subprocess.Popen(
                [sys.executable, '-I', '-B', '-c', _BOOTSTRAP, str(parent_pid),
                 str(self.owner_fd if self.owner_fd is not None else -1), *argv],
                cwd=PROJECT_ROOT, env=dict(SAFE_ENV), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                close_fds=True, pass_fds=inherited, bufsize=0)
            self._started_at = time.monotonic()
            self.cleanup.update(started=True, pid=self.pid, session=self.pid,
                                worker_argv=list(argv), owner_lock_inherited=bool(inherited),
                                parent_pid=parent_pid,
                                parent_death_policy='PR_SET_PDEATHSIG SIGKILL; parent PID recheck',
                                bootstrap='parent-death policy; RLIMIT_CORE=0; exec')
            for label in ('stdout', 'stderr'):
                pipe = getattr(self._process, label)
                self._streams[label]['pipe'] = pipe
                os.set_blocking(pipe.fileno(), False)
            root = _stat(self.pid)  # Before poll()/wait(): the leader remains attributable.
            if root['pgid'] != self.pid or root['session'] != self.pid:
                raise RuntimeError('WORKER_SESSION')
            self.cleanup['start_tick'] = root['start_tick']
            if not self._remember(root):
                raise RuntimeError('ROOT_IDENTITY')
            self._sessions.add(self.pid)
            self._tick()
            self.arm_deadline(180)
            if self.failed:
                self.stop(grace=0.0)
            return self
        except BaseException as exc:
            self._fail('START_' + type(exc).__name__)
            if self._process is not None:
                self.stop(grace=0.0)
            else:
                self.cleanup.update(quiescent=True, process_table_complete=True)
                self._seal()
            raise
        finally:
            if directory is not None:
                os.close(directory)

    def _drain(self):
        deadline = time.monotonic() + 0.03
        for label, stream in self._streams.items():
            pipe = stream['pipe']
            if pipe is None or stream['eof']:
                continue
            receipt = self.diagnostics[label]
            for _ in range(32):
                if time.monotonic() >= deadline:
                    break
                try:
                    data = os.read(pipe.fileno(), 4096)
                except BlockingIOError:
                    break
                except OSError:
                    stream['bad'] = True
                    self._fail('DIAGNOSTIC_READ_FAILED')
                    break
                if not data:
                    stream['eof'] = True
                    pipe.close()
                    receipt['complete'] = not stream['bad']
                    break
                receipt['observed_bytes'] += len(data)
                room = STREAM_CAP - receipt['retained_bytes']
                keep = data[:room]
                if len(data) > room:
                    stream['bad'] = True
                    receipt['complete'] = False
                    self._fail('DIAGNOSTIC_CAP_STOP')
                if keep and stream['fd'] is not None:
                    try:
                        while keep:
                            written = os.write(stream['fd'], keep)
                            if written <= 0:
                                raise OSError('SHORT_DIAGNOSTIC_WRITE')
                            receipt['retained_bytes'] += written
                            keep = keep[written:]
                    except OSError:
                        stream['bad'] = True
                        self._fail('DIAGNOSTIC_WRITE_FAILED')
                        os.close(stream['fd'])
                        stream['fd'] = None

    def _scan(self):
        deadline = time.thread_time() + 0.04
        table, complete = self._table()
        root = table.get(self.pid)
        if (root and self._process.returncode is None and self.pid not in self._known
                and root['start_tick'] == self.cleanup.get('start_tick')):
            self._remember(root)
        owned = {pid for pid, item in self._known.items()
                 if pid in table and table[pid]['start_tick'] == item['info']['start_tick']}
        # Present verified members anchor sessions; server-child status alone does not.
        while True:
            self._sessions.update(table[pid]['session'] for pid in owned
                                  if table[pid]['session'] == pid)
            anchored = {table[pid]['session'] for pid in owned} & self._sessions
            candidates = {pid for pid, row in table.items() if pid not in owned
                          and (row['ppid'] in owned or row['session'] in anchored)}
            accepted = set()
            for pid in candidates:
                if time.thread_time() >= deadline:
                    self._fail('OWNERSHIP_SCAN_CPU_BOUND')
                    self.cleanup.setdefault('ownership_scan_failure', {'cpu_budget_seconds':0.04,'table_complete':complete})
                    complete = False
                    break
                if self._remember(table[pid]):
                    accepted.add(pid)
            if accepted != candidates:
                complete = False
            if not accepted or time.thread_time() >= deadline:
                owned.update(accepted)
                break
            owned.update(accepted)
        for pid in owned:
            self._known[pid]['info'] = table[pid]
        if complete:
            for pid in set(self._known) - owned:
                os.close(self._known.pop(pid)['fd'])
        unknown = [row for pid, row in table.items()
                   if pid not in owned and row['session'] in self._sessions]
        if self.cleanup['subreaper_enabled']:
            unknown += [row for pid, row in table.items()
                       if row['ppid'] == os.getpid() and pid not in owned
                       and (pid, row['start_tick']) not in self._baseline_children
                       and row not in unknown]
        if unknown:
            self._fail('UNATTRIBUTED_PROCESS')
        return table, owned, complete, unknown

    def _tick(self):
        self._drain()
        table, owned, complete, unknown = self._scan()
        if self._process is not None and complete:
            # Keep the unreaped leader as an identity/session anchor until the
            # complete lineage inventory can attribute any remaining descendants.
            self._process.poll()
        zombies = []
        for pid in owned:
            row = table[pid]
            if row['state'] != 'Z':
                continue
            zombies.append(row)
            if complete and pid != self.pid and row['ppid'] == os.getpid():
                try:
                    os.waitid(os.P_PIDFD, self._known[pid]['fd'], os.WEXITED | os.WNOHANG)
                except ChildProcessError:
                    pass
                except OSError:
                    self._fail('OWNED_REAP_FAILED')
                    complete = False
        live = [table[pid] for pid in owned if table[pid]['state'] not in ('Z', 'X')]
        exit_code = None if self._process is None else self._process.returncode
        eof = all(stream['eof'] for stream in self._streams.values())
        self.cleanup.update(exit_code=exit_code, owned_live=live, owned_zombies=zombies,
                            unattributed_children=unknown, process_table_complete=complete,
                            owned_observed=self._observed, pipes_eof=eof,
                            failed=self.failed, failures=list(self.failures),
                            quiescent=bool(complete and not live and not unknown and eof
                                           and exit_code is not None and not zombies))

    @_serialized
    def poll(self):
        if self._process is None:
            return None
        if not self._sealed:
            self._tick()
            if self.failed and not self._emergency and not self._stopping:
                self._emergency = True
                self.stop(grace=0.0)
        return self._process.returncode

    def _signal(self, signum):
        sent = []
        for pid, item in self._known.items():
            row = item['info']
            if row['state'] in ('Z', 'X') or signum in item['sent']:
                continue
            try:
                signal.pidfd_send_signal(item['fd'], signum, None, 0)
                item['sent'].add(signum)
                sent.append({'pid': pid, 'start_tick': row['start_tick'],
                             'pgid': row['pgid'], 'session': row['session']})
            except ProcessLookupError:
                pass
            except OSError as exc:
                if exc.errno != errno.ESRCH:
                    self._fail('OWNED_SIGNAL_FAILED')
        if sent:
            self.cleanup['signals'].append({'signal': signum, 'processes': sent})

    def _wait_phase(self, seconds, signum):
        deadline = time.monotonic() + seconds
        while True:
            self._tick()
            if self.cleanup['quiescent']:
                return
            self._signal(signum)  # Includes newly discovered descendants.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.01, remaining))

    def _seal(self):
        for label, stream in self._streams.items():
            if stream['pipe'] is not None and not stream['eof']:
                stream['pipe'].close()
                self.diagnostics[label]['complete'] = False
            if stream['fd'] is not None:
                os.close(stream['fd'])
                stream['fd'] = None
        for item in self._known.values():
            os.close(item['fd'])
        self._known.clear()
        self._sealed = True
        self.wake()

    @_serialized
    def stop(self, grace=0.25, hard_wait=1.0):
        if any(not isinstance(value, (int, float)) or not math.isfinite(value)
               or value < 0 or value > 5 for value in (grace, hard_wait)):
            raise ValueError('STOP_BOUND_0_TO_5_SECONDS')
        if self._process is None:
            self.cleanup.update(quiescent=True, process_table_complete=True,
                                failed=self.failed, failures=list(self.failures))
            self._seal()
            return self.cleanup
        if self._sealed:
            return self.cleanup
        self._stopping = True
        started = time.monotonic()
        try:
            self.cleanup['stop_requested'] = True
            self._wait_phase(grace, signal.SIGTERM)
            if not self.cleanup['quiescent']:
                self._wait_phase(hard_wait, signal.SIGKILL)
            self._tick()
            if self.cleanup['quiescent']:
                ended = time.monotonic()
                self.cleanup.update(ended_monotonic=ended,
                    completion_deadline_met=ended <= self.cleanup.get('real_completion_deadline', ended))
                self._seal()
            else:
                self._fail('OWNED_TREE_NOT_QUIESCENT')
            self.cleanup['stop_elapsed_seconds'] = time.monotonic() - started
            return self.cleanup
        finally:
            self._stopping = False

    @_serialized
    def finish(self):
        if self._process is None or self._sealed:
            return self.stop(grace=0.0, hard_wait=0.0)
        self._tick()
        if self._process.returncode is None:
            self._fail('FINISH_BEFORE_WORKER_EXIT')
        elif self.cleanup['owned_live']:
            self.cleanup['descendants_after_worker_exit'] = True
        return self.stop()
