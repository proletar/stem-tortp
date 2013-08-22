#!/usr/local/bin/python

"""
TORtp is a simple way to implement
Tor Transparent Proxy in our GNU/Linux Box

TORtp use TOR's control port for setup
Transparen Proxy and DNS server capability
on TOR, without override default configuration file
with a custom torrc
"""

import argparse
import subprocess
import os
import pynotify
import stem
from stem.control import Controller
import stem.util
import stem.process
from stem import CircStatus
from shutil import copy2
import sys
import pwd

def notify(title, message):
    pynotify.init("TORtp")
    notice = pynotify.Notification(title, message, "/usr/share/icons/tortp/anonymous.ico")
    notice.show()
    return

def check_user():
   """ Only root can do that! """
   uid = subprocess.Popen(['id', '-u'], stdout = subprocess.PIPE)
   out = uid.stdout.read()
   if int(out) == 0:
      return os.environ['SUDO_UID']
   else:
      notify("TORtp", "Only root can do that!")
      sys.exit(1)

def get_home(user):
   """ Get user home path"""
   return pwd.getpwuid(int(user))[5]

def tortp_dir(home):
   """ Create /home/$user/.tortp """
   tortpdir = "%s/.tortp" % home
   if not os.path.exists(tortpdir):
      os.makedirs(tortpdir)
      notify("TORtp", "Directory %s created" % tortpdir)
   return tortpdir

def check_sys_dependecies():
   """ Check if all dependencies are installed """
   devnull = open(os.devnull,"w")
   dnsmasq = subprocess.call(["dpkg","-s","dnsmasq"],stdout=devnull,stderr=subprocess.STDOUT)
   if dnsmasq != 0:
      print "Package dnsmasq not installed"
   tor = subprocess.call(["dpkg","-s","tor"],stdout=devnull,stderr=subprocess.STDOUT)
   if tor != 0:
       print "Package tor not installed"
   devnull.close()

def iptables_clean():
   """ This function remove all iptables rules """
   subprocess.call(['iptables', '-F'])
   subprocess.call(['iptables', '-X'])
   subprocess.call(['iptables', '-t', 'nat', '-F'])
   subprocess.call(['iptables', '-t', 'nat', '-X'])

def iptables_up(tortpdir, user):
   """ This function make backup and add iptables rules for redirect all user traffic to tortp """
   ipt = open("%s/iptables.txt", "w")
   subprocess.call(['iptables-save'], stdout=ipt)
   ipt.close()
   # Redirect DNSTor port (9053)
   subprocess.call(['iptables', '-t', 'nat', '-A', 'OUTPUT', '!', '-o', 'lo', '-p', 'udp', '-m', 'owner', '--uid-owner', '%s' % user, '-m', 'udp', '--dport', '53', '-j', 'REDIRECT', '--to-ports', '9053'])
   subprocess.call(['iptables', '-t', 'filter', '-A', 'OUTPUT', '-p', 'udp', '-m', 'owner', '--uid-owner', '%s' % user, '-m', 'udp', '--dport', '53', '-j', 'ACCEPT'])
   subprocess.call(['iptables', '-t', 'filter', '-A', 'OUTPUT', '-p', 'tcp', '-m', 'owner', '--uid-owner', '%s' % user, '-m', 'tcp', '--dport', '53', '-j', 'ACCEPT'])
   # Redirect to Transparent Proxy Tor (9040)
   subprocess.call(['iptables', '-t', 'nat', '-A', 'OUTPUT', '!', '-o', 'lo', '-p', 'tcp', '-m', 'owner', '--uid-owner', '%s' % user, '-m', 'tcp', '-j', 'REDIRECT', '--to-ports', '9040'])
   subprocess.call(['iptables', '-t', 'filter', '-A', 'OUTPUT', '-p', 'tcp', '-m', 'owner', '--uid-owner', '%s' % user, '-m', 'tcp', '--dport', '9040', '-j', 'ACCEPT'])
   subprocess.call(['iptables', '-t', 'filter', '-A', 'OUTPUT', '!', '-o', 'lo', '-m', 'owner', '--uid-owner', '%s' % user, '-j', 'DROP'])
   subprocess.call(['iptables', '-t', 'nat', '-A', 'OUTPUT', '-p', 'tcp', '-m', 'owner', '--uid-owner', '%s' % user, '-m', 'tcp', '--syn', '-d', '127.0.0.1', '--dport', '9051', '-j', 'ACCEPT'])

def iptables_down(tortpdir):
   """ Restore original iptables rules """
   try:
      subprocess.call('iptables-restore < %s/iptables.txt' % tortpdir, shell=True)
      os.remove("%s/iptables.txt" % tortpdir)
   except IOError as e:
      iptables_clean()
      print e

def resolvconf(tortpdir):
   """ Backup and modify resolv configuration file """
   try:
      copy2("/etc/resolv.conf",tortpdir)
   except IOError as e:
      print e
   resolv = open('/etc/resolv.conf', 'w')
   resolv.write('nameserver 127.0.0.1\n')
   resolv.close()

def dnsmasq(tortpdir):
   """ Backup and modify dnsmasq configuration file """
   try:
      copy2("/etc/dnsmasq.conf",tortpdir)
   except IOError as e:
      print e
   dmasq = open('/etc/dnsmasq.conf', 'w')
   dmasq.write('no-resolv\n')
   dmasq.write('server=127.0.0.1#9053\n')
   dmasq.write('listen-address=127.0.0.1\n')
   dmasq.close()

def enable_tordns():
   """ Use Tor's ControlPort for enable TorDNS"""
   with Controller.from_port(port = 9051) as controller:
      controller.authenticate()
      controller.set_options({"DNSPort": "9053", "AutomapHostsOnResolve": "1", "AutomapHostsSuffixes": ".exit,.onion"})

def enable_torproxy():
   """ Use Tor's ControlPort for enable Tor Transparent Proxy """
   with Controller.from_port(port = 9051) as controller:
      controller.authenticate()
      controller.set_options({"VirtualAddrNetwork": "10.192.0.0/10", "TransPort": "9040", "TransListenAddress": "127.0.0.1","AvoidDiskWrites": "1", "WarnUnsafeSocks": "1"})

def exit_info():
   """ Print info about my exit node """
   with Controller.from_port(port = 9051) as controller:
      controller.authenticate()
      for circ in controller.get_circuits():
         if circ.status != CircStatus.BUILT:
            continue
         exit_fp, exit_nickname = circ.path[-1]
         exit_desc = controller.get_network_status(exit_fp, None)
         exit_address = exit_desc.address if exit_desc else 'unknown'
         print "Exit relay"
         print "  fingerprint: %s" % exit_fp
         print "  nickname: %s" % exit_nickname
         print "  address: %s" % exit_address

def tor_new():
   """ Create a new tor circuit """
   try:
      stem.socket.ControlPort(port = 9051)
   except stem.SocketError as exc:
      print "Unable to connect to port 9051 (%s)" % exc
      print "Please add 'ControlPort 9051' on your /etc/tor/torrc configuration"
      sys.exit(1)
   with Controller.from_port(port = 9051) as controller:
      controller.authenticate()
      controller.signal(stem.Signal.NEWNYM)
      notify("TORtp", "New Tor circuit created")

def start(tortpdir):
   try:
      stem.socket.ControlPort(port = 9051)
   except stem.SocketError as exc:
      print "Unable to connect to port 9051 (%s)" % exc
      print "Please add 'ControlPort 9051' on your /etc/tor/torrc configuration"
      sys.exit(1)
   if os.path.exists("%s/resolv.conf" % tortpdir) and os.path.exists("%s/dnsmasq.conf" % tortpdir) and os.path.exists("%s/iptables.txt" % tortpdir):
      print "TORtp is already running"
      sys.exit(1)
   else:
      iptables_clean()
      iptables_up(tortpdir, check_user())
      enable_tordns()
      enable_torproxy()
      resolvconf(tortpdir)
      dnsmasq(tortpdir)
      # restart dnsmasq
      devnull = open(os.devnull,"w")
      subprocess.call(['/etc/init.d/dnsmasq', 'restart'], stdout=devnull)
      devnull.close()
      notify("TORtp", "Tor Transparent Proxy enabled")

def stop(tortpdir):
   """ Restore all original files"""
   try:
      copy2("%s/resolv.conf" % tortpdir, "/etc")
      copy2("%s/dnsmasq.conf" % tortpdir, "/etc")
      os.remove("%s/resolv.conf" % tortpdir)
      os.remove("%s/dnsmasq.conf" % tortpdir)
   except IOError as e:
      print e
      print "TORtp seems already disabled"
      sys.exit(1)
   # restart tor and dnsmasq
   devnull = open(os.devnull,"w")
   subprocess.call(['/etc/init.d/dnsmasq', 'restart'], stdout=devnull)
   subprocess.call(['/etc/init.d/tor', 'reload'], stdout=devnull)
   devnull.close()
   iptables_down(tortpdir)
   notify("TORtp", "Tor Transparent Proxy disabled")

def main(arg):
   parser = argparse.ArgumentParser()
   parser.add_argument("do", help="start | stop | new | info")
   args = parser.parse_args()
   if args.do == "start":
      start(tortp_dir(get_home(check_user())))
   if args.do == "stop":
      stop(tortp_dir(get_home(check_user())))
   if args.do == "new":
      tor_new()
   if args.do == "info":
      exit_info()
   if args.do != "start" and args.do != "stop" and args.do != "new" and args.do != "info":
      print 'Type "tortp.py -h" for options'

if __name__ == '__main__':
   main(sys.argv[1:])
