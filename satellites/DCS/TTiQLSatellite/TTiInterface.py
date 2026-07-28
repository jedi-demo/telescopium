"""
SPDX-FileCopyrightText: 2024 DESY and the Constellation authors
SPDX-License-Identifier: EUPL-1.2

Abstract TCP/IP socket interface for AIM-TTi QL355TP power supplies.
"""

from abc import ABCMeta, abstractmethod
import socket
import time
from threading import Lock

class TTiInterface(metaclass=ABCMeta):
    # 
    def __init__(self, ip_address: str, timeout: float = 5.0):
        self._ip = ip_address
        self._port = 9221
        self._timeout = timeout
        self._socket = None
        self._lock = Lock()

    # Network helper functions

    def connect(self):
        """
        Establish TCP/IP socket connection to the device
        """
        with self._lock:
            if self._socket is None:
                try:
                    self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    self._socket.settimeout(self._timeout)
                    self._socket.connect((self._ip, self._port))
                except Exception as e:
                    self._socket = None
                    print(f"Socket Connection Error {self._ip}:{self._port} -> {e}")
                    raise

    def close(self):
        """
        Close TCP/IP socket connection    
        """
        with self._lock:
            if self._socket:
                try:
                    self._socket.close()
                except:
                    pass
                self._socket = None

    def _write(self, command: str):
        """
        Write command to socket
        """
        if not self._socket: self.connect()
        
        with self._lock:
            try:
                msg = f"{command}\n".encode('ascii')
                self._socket.sendall(msg)
                time.sleep(0.05)
            except (BrokenPipeError, ConnectionResetError, socket.error):
                self.close()
                self.connect()
                self._socket.sendall(f"{command}\n".encode('ascii'))

    def _query(self, command: str) -> str:
        """
        Query the device and return the response
        """
        if not self._socket: self.connect()
        
        with self._lock:
            try:
                # Send
                self._socket.sendall(f"{command}\n".encode('ascii'))
                
                # Wait for device processing
                time.sleep(0.3)

                # Read
                response = self._socket.recv(1024)
                return response.decode('ascii').strip()
                
            except (BrokenPipeError, ConnectionResetError, socket.error) as e:
                print(f"Socket lost: {e}. Reconnecting...")
                self.close()
                self.connect()
                
                # Retry
                self._socket.sendall(f"{command}\n".encode('ascii'))
                time.sleep(0.3)
                return self._socket.recv(1024).decode('ascii').strip()
    
    # Device functions

    @abstractmethod
    def identify(self) -> str: 
        # Identify the device   
        pass
    @abstractmethod
    def initialize(self): 
        # Initialize the device
        pass
    @abstractmethod
    def set_output(self, channel: int, enable: bool): 
        # Set the output state of a channel
        pass
    @abstractmethod
    def set_voltage(self, channel: int, voltage: float): 
        # Set the voltage of a channel
        pass
    @abstractmethod
    def set_current_limit(self, channel: int, current: float): 
        # Set the current limit of a channel
        pass
    @abstractmethod
    def set_ovp(self, channel: int, voltage: float): 
        # Set the over-voltage protection of a channel
        pass
    @abstractmethod
    def set_ocp(self, channel: int, current: float): 
        # Set the over-current protection of a channel
        pass
    @abstractmethod
    def get_voltage_readback(self, channel: int) -> float: 
        # Get the voltage readback of a channel
        pass
    @abstractmethod
    def get_current_readback(self, channel: int) -> float: 
        # Get the current readback of a channel
        pass
    @abstractmethod
    def get_limit_status(self, channel: int) -> int: 
        # Get the limit status of a channel
        pass
    @abstractmethod
    def get_execution_error(self) -> int: 
        # Get the execution error
        pass
