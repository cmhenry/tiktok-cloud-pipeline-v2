"""
Name: tt_15_transfer_sound.py
Date: Nov 17. 2025
Author: BG
Purpose: transfer sounds from AWS
"""

import sys, time, os
import logging
import requests, json
import subprocess
import re
from filelock import FileLock
import redis

### Utils ###

def time_to_str(time_int): 
    return str(time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time_int)))

def remove_lock_file(lock_path): 
    if os.path.exists(lock_path): 
        os.remove(lock_path)

def manage_lock_status(lock_path: str) -> bool:
    """
    Checks for the existence of `lock_path`.
    If it exists, checks whether it was modified within the past 60 minutes:
      - If yes (recently modified), returns False (do nothing).
      - If no, updates (recreates) the file and returns True.
    If the lock file does not exist, creates it and returns True.
    
    :param lock_path: Path to the lock file
    :return: True if the file was created or updated, False otherwise
    """
    # 60 minutes in seconds
    sixty_minutes = 60 * 60
    
    with FileLock(lock_path + ".lock"):
        if os.path.exists(lock_path):
            # Get the last modification time
            last_modified_time = os.path.getmtime(lock_path)
            # How long ago it was modified
            time_since_mod = time.time() - last_modified_time
            
            # If modified within the last 60 minutes, do nothing
            if time_since_mod < sixty_minutes:
                return False
            else:
                # Otherwise, "recreate" or update the file timestamp
                with open(lock_path, 'w') as f:
                    trash = f.write('u\n')
                return True
        else:
            # File does not exist, create it
            with open(lock_path, 'w') as f:
                trash = f.write('n\n')
            return True
        


def setup_logger(log_file_name, log_directory='', debug = False, to_stdout=False):
    """
    Configures and returns a logger with a timed rotating file handler. Allows us to bin logs by day
    :param log_file_name:
    :param log_directory:
    :param level:
    :param to_stdout:
    :return:
    """
    level = logging.DEBUG if debug else logging.INFO
    logger = logging.getLogger(log_file_name)
    
    if not logger.handlers:
        logger.setLevel(level)
        formatter = logging.Formatter('%(asctime)s.%(msecs)03d   %(message)s', datefmt='%H:%M:%S')
        
        if to_stdout:
            handler = logging.StreamHandler()  # Logs to stdout
        else:
            if not os.path.exists(log_directory):
                os.makedirs(log_directory)
            log_file_path = os.path.join(log_directory, log_file_name)
            handler = logging.handlers.TimedRotatingFileHandler(
                filename=log_file_path, when='midnight', interval=1, backupCount=7
            )
            handler.suffix = "%Y-%m-%d.log"
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger

def pad(string, w=10):
    """
    
    :param string:
    :param w:
    :return:
    """
    string = str(string)
    if len(string) >= w:
        return string
    else:
        return string + (" " * (w - len(string)))

def log_message(message, logger, ip_address='', level='info'):
    """
    This function is a helper method for making log statements. It formats
    messages to include the process id and name of the function where code is executing
    :param message:
    :param logger:
    :param ip_address:
    :param level:
    :return:
    """
    # get process id
    process_id = pad(os.getpid(), 8)
    
    # use previously initialized logger
    if level == 'info':
        logger.info(f'{process_id}\tinf\t{message}')
    elif level == 'debug':
        logger.debug(f'{process_id}\tdeb\t{message}')
    elif level == 'warning':
        send_slack_text_message(message.strip(), webhook='ttm_7_warning')
        logger.warning(f'{process_id}\tdeb\t{message}')
    elif level == 'error':
        send_slack_text_message(message.strip(), webhook='ttm_5_errors')
        logger.error(f'{process_id}\terr\t{message}')

WEBHOOKS = {
    'ttm_5_errors': "https://hooks.slack.com/services/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",
    "ttm_7_warning": "https://hooks.slack.com/services/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", 
	"ttm_0_transfer_zrh": "https://hooks.slack.com/services/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",
}

def send_slack_text_message(text, webhook='tiktok_monitor'):
    
    # init slack vars
    if webhook in list(WEBHOOKS.keys()): 
        slack_webhook = WEBHOOKS[webhook]
    else: 
        slack_webhook = WEBHOOKS["ttm_5_errors"]
    
    if webhook in ["ttm_5_errors", "ttm_7_warning"]: 
        text = f"{str(os.getpid())} {text}"
    
    slack_data = {"text": text}
    
    byte_length = str(sys.getsizeof(slack_data))
    headers = {'Content-Type': "application/json", 'Content-Length': byte_length}
    
    response = requests.post(slack_webhook, data=json.dumps(slack_data), headers=headers)
    if response.status_code != 200:
        print(f"{response.status_code}: {response.text}")


def file_exists(path, remote_host=None, logger=None):
    try:
        
        if remote_host is None:
            return os.path.exists(path)
        else:
            cmd = [
                "ssh",
                "-F", SSH_CONFIG_FILE,
                remote_host,
                f'[[ -e "{path}" ]] && echo "exists" || echo "missing"'
            ]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            return 'exist' in result.stdout
    
    except subprocess.CalledProcessError as err:
        return None
    
    except OSError as err:
        return None

def get_file_size(path, remote_host = None, logger = None):
    """
    Use 'wc -c' to get the file size in bytes on the remote host.
    """
    try:
        if remote_host is not None: 
            cmd = [
                "ssh", 
                "-F", SSH_CONFIG_FILE,
                remote_host,
                f"wc -c '{path}'"
            ]
        else: 
            cmd = ["wc", "-c", path]
        
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        size_str = result.stdout.strip().split()[0]
        return int(size_str) if size_str.isdigit() else 0
    
    except subprocess.CalledProcessError as err:
        msg = (
            f"[SSH ERROR] Failed getting file size  {path!r} on {remote_host}.\n"
            f"Return code: {err.returncode}\n"
            f"STDERR: {err.stderr}"
        )
        if logger is not None:
            log_message(msg, logger, level = "error")
        else:
            print(msg)
        return None
    
    except OSError as err:
        msg = f"[OS ERROR] Failed getting file size {path!r}: {err}"
        if logger is not None:
            log_message(msg, logger, level = "error")
        else:
            print(msg)
        return None

def list_files(path, remote_host = None, latency_min = 10, logger = None, type = "f"):
    """List files in a remote directory using SSH that haven't changed in the last 10 minutes."""
    try:
        if remote_host is not None: 
            cmd = [
                "ssh",
                "-F", SSH_CONFIG_FILE,
                f"{remote_host}",
                f"find {path} -type {type} -mmin +{latency_min}"
            ]
        else: 
            cmd = [
                "find", path, f"-type {type} -mmin +{latency_min}"
            ]
        output = subprocess.check_output(cmd).decode().strip()
        return output.split("\n") if output else []
    except subprocess.CalledProcessError as err:
        msg = (
            f"[SSH ERROR] Failed listing files{path!r} on {remote_host}.\n"
            f"Return code: {err.returncode}\n"
            f"STDERR: {err.stderr}"
        )
        if logger is not None:
            log_message(msg, logger, level = "error")
        else:
            print(msg)
        return []
    
    except OSError as err:
        msg = f"[OS ERROR] Failed listing files{path!r}: {err}"
        if logger is not None:
            log_message(msg, logger, level = "error")
        else:
            print(msg)
        return []

def remove_file(path, remote_host = None, logger = None):
    """
    Remove the file on the remote host. 
    """
    try:
        if remote_host is not None: 
            cmd = [
                "ssh",
                "-F", SSH_CONFIG_FILE,
                remote_host,
                f"rm -f '{path}'"
            ]
            subprocess.run(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
                text=True, check=True
            )
        else: 
            os.remove(path)
    except subprocess.CalledProcessError as err:
        msg = (
            f"[SSH ERROR] Failed removing files {path!r} on {remote_host}.\n"
            f"Return code: {err.returncode}\n"
            f"STDERR: {err.stderr}"
        )
        if logger is not None:
            log_message(msg, logger, level = "error")
        else:
            print(msg)
        return False
    
    except OSError as err:
        msg = f"[OS ERROR] Local error removing files {path!r}: {err}"
        if logger is not None:
            log_message(msg, logger, level = "error")
        else:
            print(msg)
        return False

def transfer_sound_zrh(source_path, dest_path, source_host = None, secure = True, logger = None, remove = True):
    """Transfer a file directly from AWS to Greene via SCP with ProxyJump."""
    
    start_time = time.time()
    file_path = os.path.basename(source_path)
    dest_folder = os.path.basename(dest_path)
    source = f"{source_host}:{source_path}"
    
    lock_path = f"{TRANSFER_LOCK_FOLDER}/tt-aws_zrh_{file_path.replace('/', '_')}.lock"
    lock_status = manage_lock_status(lock_path)
    
    
    if not lock_status: 
        msg = f"\t\ttt-aws to zrh\t{dest_folder}\t{file_path}\tSkipped because of lock status"
        log_message(msg, logger)
        return False
    else: 
        try:
            if secure: 
                source_size = None
                size_try = 0
                while source_size is None and size_try < 10: 
                    size_try += 1
                    source_size = get_file_size(
                        source_path, 
                        source_host, 
                        logger = logger
                    )
                
                if source_size is None: 
                    log_message(f"\t\ttt-aws to zrh\t{dest_folder}\t{file_path}\t\tUnable while getting source size", logger, level="warning")
                    remove_lock_file(lock_path)
                    return False
                
                size_msg = f"{source_size/1024/1024:.1f} Mb "
            else: 
                size_msg = ""
        except Exception as e: 
            log_message(f"\t\ttt-aws to zrh\t{dest_folder}\t{file_path}\t\tError while getting source size\t{e}", logger, level="error")
            remove_lock_file(lock_path)
            return False
        
        log_first_part = f"\t\ttt-aws to zrh\t{dest_folder}\t{file_path}\t\t{size_msg}\t"
        log_message(log_first_part, logger)
        
        try: 
            # Use ProxyJump to chain AWS and Greene connections
            cmd = [
                "scp",
                "-3", 
                "-q",
                "-F", SSH_CONFIG_FILE,
                source,
                dest_path
            ]
            
            try: 
                subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
                    text=True, check=True
                )
            except subprocess.CalledProcessError as err:
                msg = (
                    f"[SSH ERROR] Copying files from {source} on tt-aws.\n"
                    f"Return code: {err.returncode}\n"
                    f"STDERR: {err.stderr}"
                )
                log_message(msg, logger, level = "error")
                remove_lock_file(lock_path)
                return False
            
            except OSError as err:
                msg = f"[OS ERROR] Copying files from {source} on tt-aws.\n"
                log_message(msg, logger, "error")
                remove_lock_file(lock_path)
                return False
            
            # If secure is True, then check for size match between files
            if secure: 
                dest_size = None
                size_try = 0
                while dest_size is None and size_try < 10: 
                    size_try += 1
                    dest_size = get_file_size(
                        path = f"{dest_path}/{file_path}", 
                        remote_host=None, 
                        logger = logger
                    )
                
                if source_size != dest_size:
                    log_message(f"\t\ttt-aws to local\t{dest_folder}\t{file_path}\t\tSize mismatch during transfer {source_size} {dest_size}", logger, level="error")
                    remove_lock_file(lock_path)
                    return False
                    
                
                size_msg = f"{dest_size/1024/1024:.1f} Mb "
            else: 
                size_msg = ""
            
            if remove: 
                try: 
                    remove_file(
                        source_path, 
                        source_host, 
                        logger = logger
                    )
                except Exception as e: 
                    log_message(f"{log_first_part}\tError during deletion\t{e}", logger, level="error")
                    remove_lock_file(lock_path)
                    return False
            
            duration = time.time() - start_time
            
            msg = f"{log_first_part}\tSucess:  {size_msg}in {duration:.2f} secs"
            log_message(msg, logger)
            if secure: 
                send_slack_text_message(msg, webhook = "ttm_0_transfer_zrh")
            remove_lock_file(lock_path)
            return True
        except Exception as e:
            log_message(f"{log_first_part}\tError during transfer to zrh\t{str(e)[:200]}", logger, level="error")
            remove_lock_file(lock_path)
            return False

###### To fill #####

# Redis for pipeline queue
REDIS_HOST = "localhost"
REDIS_PORT = 6379
QUEUE_UNPACK = "list:unpack"

ROOT_PATH = "/home/ubuntu"
LOG_FOLDER = f"{ROOT_PATH}/logs"
DEST_FOLDER = f"{ROOT_PATH}/export_sound"
SSH_CONFIG_FILE = f"{ROOT_PATH}/.ssh/ssh_config"
TRANSFER_LOCK_FOLDER = f"{ROOT_PATH}/transfer_locks/logs"

for tmp_folder in [LOG_FOLDER, DEST_FOLDER, os.path.dirname(SSH_CONFIG_FILE, TRANSFER_LOCK_FOLDER)]: 
    os.makedirs(tmp_folder, exist_ok=True)

# Write this to SSH_CONFIG_FILE after fixing the pathcs and adding private key to AWS
# Make sure to give proper rights: chmod 600 $SSH_CONFIG_FILE
# Host tt-zrh
#     HostName ec2-54-172-69-64.compute-1.amazonaws.com
#     User ec2-user
#     UserKnownHostsFile /tiktok_kubernetes/.ssh/known_hosts
#     StrictHostKeyChecking accept-new
#     IdentityFile /tiktok_kubernetes/.ssh/id_rsa_ec2

###### To fill #####

DEBUG = True
INTERACTIVE = hasattr(sys, 'ps1')
# Only printing to stdout when run in interactive session
TO_SDTOUT = False
if INTERACTIVE:
	TO_SDTOUT = True
# This is where the video are on the VM
SOURCE_FOLDER = "/mnt/hub/export/sound"
# The script is started once every hour five minutes after the hour
MINUTE_TO_RESTART_SCRIPT = 5
AWS_CONTENT_VM_HOST = "tt-zrh"

start_hour = time.gmtime().tm_hour

# Initialize logger
logger = setup_logger(
    log_file_name=f'transfer_aws_zrh.log',
    log_directory = LOG_FOLDER,
    debug=DEBUG,
    to_stdout=TO_SDTOUT
)

msg = f"Starting transfer SOUND AWS to ZRH"
log_message(msg, logger)
send_slack_text_message(msg, webhook = "ttm_0_transfer_zrh")

# Initialize Redis client
try:
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    redis_client.ping()  # Test connection
    log_message("Redis connection successful", logger)
except Exception as e:
    log_message(f"Redis connection failed: {e}", logger, level="error")
    redis_client = None  # Continue without Redis if it fails

while not (start_hour != time.gmtime().tm_hour and time.gmtime().tm_min >= MINUTE_TO_RESTART_SCRIPT):
        
	start_time = time.time()
	
	source_files = [i for i in list_files(SOURCE_FOLDER, AWS_CONTENT_VM_HOST, latency_min = 10, logger = logger) if not i.endswith(".lock")]
	
	files_copied = 0
	files_skipped = 0
	
	if len(source_files) > 0: 
		log_message(f"\tSound {len(source_files)} found", logger)
		
		for source_file in source_files[:50]: 
			
			if start_hour != time.gmtime().tm_hour and time.gmtime().tm_min >= MINUTE_TO_RESTART_SCRIPT:
				break
			
			if file_exists(source_file, AWS_CONTENT_VM_HOST): 
				# 2. Transfer file directly from AWS to Greene
				transfer_result = transfer_sound_zrh(
					source_path = source_file, 
					dest_path = DEST_FOLDER, 
					source_host = AWS_CONTENT_VM_HOST, 
					logger=logger, 
					secure = True,
                    # TODO: once this is ready set this to true to remove the file
					remove = False
				)
			else: 
				transfer_result = False
			
			if not transfer_result:
				# Transfer failed; skip verification & removal
				files_skipped += 1
				continue
			else: 
				files_copied += 1
	
	if files_copied + files_skipped:
		log_message(f"\t\tComplete for Sound\t{files_copied} + {files_skipped}\t in {time.time() - start_time:.2f} secs\n\n", logger)
	
	time.sleep(60)

msg = f"Finished transfer AWS to Greene\n\n"
send_slack_text_message(msg, webhook = "ttm_0_transfer_zrh")
log_message(msg, logger)
