"""No model uploads are allowed after bad or unverified system health."""
from contextlib import contextmanager
from unittest.mock import patch
import json,unittest
import board_connection


@contextmanager
def connection(*args):yield object()


class PreflightTests(unittest.TestCase):
    def blocked(self,reply):
        commands=[]
        def run(transport,command,**kwargs):commands.append(command);return reply
        with patch.object(board_connection,'connect_checked',connection),patch.object(board_connection,'run',run),patch.object(board_connection,'sftp') as upload,patch.object(board_connection.FloorBackend,'close'):
            with self.assertRaises(RuntimeError):board_connection.FloorBackend('test-only')
            upload.assert_not_called()
        self.assertEqual(len(commands),1);self.assertNotIn('mkdir ',commands[0])
    def test_shell_io_error_blocks_model_loading(self):self.blocked(dict(code=-1,stdout='',stderr='bash: /etc/bash.bashrc: Input/output error'))
    def test_ext4_errors_block_model_loading(self):
        self.blocked(dict(code=0,stdout=json.dumps(dict(boot='test',ram_free=200*1024**2,mounts={'/':'rw','/dev/shm':'rw'},ext4={'card':'1'})),stderr=''))
    def test_readonly_root_blocks_model_loading(self):
        self.blocked(dict(code=0,stdout=json.dumps(dict(boot='test',ram_free=200*1024**2,mounts={'/':'ro','/dev/shm':'rw'},ext4={'card':'0'})),stderr=''))
    def test_mmc_read_error_blocks_even_with_zero_ext4_counter(self):
        self.blocked(dict(code=0,stdout=json.dumps(dict(boot='test',ram_free=200*1024**2,mounts={'/':'rw','/dev/shm':'rw'},ext4={'card':'0'},kernel_query_code=0,storage_errors=['I/O error, dev mmcblk1'])),stderr=''))
    def test_unavailable_kernel_query_blocks_model_loading(self):
        self.blocked(dict(code=0,stdout=json.dumps(dict(boot='test',ram_free=200*1024**2,mounts={'/':'rw','/dev/shm':'rw'},ext4={'card':'0'},kernel_query_code=1,storage_errors=[])),stderr=''))


if __name__=='__main__':unittest.main()
