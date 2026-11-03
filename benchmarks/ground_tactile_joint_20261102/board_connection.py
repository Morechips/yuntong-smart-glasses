"""Candidate-only connection patience; fixed host key and bounded IO unchanged."""
import base64,contextlib,hashlib,socket,time,uuid,shlex,json
import paramiko
import paths
from rdk_io import HOST,EXPECTED_KEY,run,sftp
from device_settings import USER,require_x5_identity
from backends import X5Backend,BIN


@contextlib.contextmanager
def connect_checked(password):
    require_x5_identity()
    sock=socket.create_connection((HOST,22),timeout=5);transport=paramiko.Transport(sock)
    try:
        transport.banner_timeout=35
        transport.start_client(timeout=35)
        fingerprint='SHA256:'+base64.b64encode(hashlib.sha256(transport.get_remote_server_key().asbytes()).digest()).decode().rstrip('=')
        if fingerprint!=EXPECTED_KEY:raise RuntimeError('Unexpected X5 host key')
        transport.auth_timeout=20;transport.auth_password(USER,password)
        yield transport
    finally:transport.close()


class FloorBackend(X5Backend):
    """Same BIN, worker and framed infer/cleanup; only startup wait differs."""
    def __init__(self,password):
        self.run=run;self.ctx=connect_checked(password);self.transport=self.ctx.__enter__()
        self.channel=None;self.reader=None;self.writer=None
        self.remote='/dev/shm/yuntong_floor_demo_joint_'+uuid.uuid4().hex[:12]
        try:
            health_code="""import json,pathlib,shutil,subprocess,re
mounts={r.split()[1]:r.split()[3] for r in pathlib.Path('/proc/mounts').read_text().splitlines() if r.split()[1] in ['/','/dev/shm']}
kernel=subprocess.run(['sudo','-n','dmesg','--color=never'],capture_output=True,text=True,timeout=5)
errors=[line for line in kernel.stdout.splitlines() if re.search(r'mmcblk[0-9]+:.*(recovery failed|error)|I/O error, dev mmc|Buffer I/O error.*mmc|EXT4-fs.*(error|aborted)|mmc[0-9]+:.*(timeout|timed out|error -)',line,re.I)]
print(json.dumps(dict(boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),ram_free=shutil.disk_usage('/dev/shm').free,mounts=mounts,ext4={p.parent.name:p.read_text().strip() for p in pathlib.Path('/sys/fs/ext4').glob('*/errors_count')},kernel_query_code=kernel.returncode,storage_errors=errors)))
"""
            pre=run(self.transport,'python3 -c '+shlex.quote(health_code),timeout=20)
            if pre['code']!=0:raise RuntimeError('X5 health preflight failed: '+json.dumps(pre,ensure_ascii=False))
            health=json.loads(pre['stdout']);self.boot=health['boot']
            if set(health['mounts'])!={'/','/dev/shm'} or any('rw' not in flags.split(',') for flags in health['mounts'].values()):raise RuntimeError('X5 mount health not verified rw')
            if not health['ext4'] or any(value!='0' for value in health['ext4'].values()):raise RuntimeError('X5 ext4 error counters not verified zero')
            if health.get('kernel_query_code')!=0 or health.get('storage_errors'):raise RuntimeError('X5 kernel storage health not verified')
            if health['ram_free']<=64*1024**2:raise RuntimeError('Insufficient RAM tmpfs')
            if run(self.transport,'mkdir '+shlex.quote(self.remote))['code']!=0:raise RuntimeError('Cannot create RAM directory')
            files=[(BIN,'model.bin'),(paths.BASE/'board_worker.py','board_worker.py'),(paths.BASE/'demo_core.py','demo_core.py')]
            with sftp(self.transport) as client:
                for source,name in files:
                    client.put(str(source),self.remote+'/'+name)
                    with client.open(self.remote+'/'+name,'rb') as stream:
                        if hashlib.sha256(stream.read()).digest()!=hashlib.sha256(source.read_bytes()).digest():raise ValueError('Upload SHA mismatch')
            self.channel=self.transport.open_session();self.channel.settimeout(12)
            self.channel.exec_command('PYTHONDONTWRITEBYTECODE=1 python3 -u '+shlex.quote(self.remote+'/board_worker.py')+' '+shlex.quote(self.remote+'/model.bin'))
            self.reader=self.channel.makefile('rb');self.writer=self.channel.makefile('wb');ready=self._read()
            if not ready.get('ready'):raise RuntimeError('Floor worker not ready')
            self.channel.settimeout(3)
            self.metadata=dict(backend=self.name,remote=self.remote,boot=self.boot,worker=ready,preflight=health,model_sha256=hashlib.sha256(BIN.read_bytes()).hexdigest(),candidate_handshake_timeout_s=35)
        except Exception:self.close();raise
