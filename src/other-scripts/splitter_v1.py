#!/usr/bin/python -O

# Recibe un stream y lo sirve v'ia UDP. Posee hilos de gesti'on del
# cluster.

import time
import sys
import socket
from threading import Thread
import struct

source_hostname = '150.214.150.68'
source_port = 4551
listening_port = 9999
buffer_size = 256

def get_peer_connection_socket():
    # {{{

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        # This does not work in Windows systems.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except:
        pass

    sock.bind( ('', listening_port) )
    #sock.listen(5)
    sock.listen(socket.SOMAXCONN)   # Set the connection queue to the max!

    return sock

    # }}}
# Socket to manage the cluster (churn).
peer_connection_sock = get_peer_connection_socket()

def create_cluster_sock(listening_port):
    # {{{ 
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # This does not work in Windows systems !!
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except:
        pass
    sock.bind(('', listening_port))
    #peer_socket.bind(('',peer_connection_sock.getsockname()[PORT]))

    return sock
    # }}}
# Socket to send the media to the cluster.
cluster_sock = create_cluster_sock(listening_port)

# The list of peers in the cluster. There will be always a peer in the
# list of peers that, by default, is running in the same host than the
# splitter, listening to the port listening_port+1. Notice that you
# can replace this end-point by any other you want, for example, in a
# different host.
peer_list = [('127.0.0.1',listening_port+1)]

# Destination peers of the block, indexed by a block number. Used to
# find the peer to which a block has been sent.
destination_of_block = [('0.0.0.0',0) for i in xrange(buffer_size)]

# Unreliaility of the peers, indexed by (the end-point of) the
# peer. Counts the number of times a peer has not re-transmitted a
# packet.
unreliability = {}

# Complaining rate of a peer. Sometimes the complaining peer has not
# enough bandwidth in his download link. In this case, the peevish
# peers should be rejected from the cluster.
complains = {}

# Useful definitions.
IP_ADDR = 0
PORT = 1

# This is used to stop the child threads. They will be alive only
# while the main thread is alive.
main_alive = True

# When a peer want to join a cluster, first it must establish a TCP
# connection with the splitter. In that connection, the splitter sends
# to the incomming peer the list of peers. Notice that the
# transmission of the list of peers (something that could need some
# time if the cluster is big or the peer is slow) is done in a
# separate thread. This helps to avoid a DoS (Denial-of-Service)
# attack.

# Handle the arrival of a peer.
class handle_one_arrival(Thread):
    # {{{

    peer_serve_socket = ""
    peer = ""
    
    def __init__(self, peer_serve_socket, peer):
        Thread.__init__(self)
        self.peer_serve_socket = peer_serve_socket
        self.peer = peer
        
    def run(self):
        global peer_list
        global unreliability

        print self.peer_serve_socket.getsockname(), \
            'Accepted connection from peer', \
            self.peer

        print self.peer_serve_socket.getsockname(), \
            'Sending the list of peers ...',

        # Sends the size of the list of peers.
        message = struct.pack("H", socket.htons(len(peer_list)))
        self.peer_serve_socket.sendall(message)

        # Send the list of peers.
        for p in peer_list:
            message = struct.pack(
                "4sH", socket.inet_aton(p[IP_ADDR]),
                socket.htons(p[PORT]))
            self.peer_serve_socket.sendall(message)
            print p

        print 'done'

        self.peer_serve_socket.close()
        peer_list.append(self.peer)
        unreliability[self.peer] = 0
        complains[self.peer] = 0
    # }}}

# The daemon which runs the "handle_one_arrival" threads. 
class handle_arrivals(Thread):
    # {{{

    def __init__(self):
        Thread.__init__(self)

    def run(self):
        while main_alive:
            peer_serve_socket, peer = peer_connection_sock.accept()
            handle_one_arrival(peer_serve_socket, peer).start()

    # }}}
handle_arrivals().start()

# A new daemon to administrate the cluster.
class listen_to_the_cluster(Thread):
    # {{{

    def __init__(self):
        Thread.__init__(self)

    def run(self):
        while main_alive:
            # {{{

            # Usually, peers complain about lost blocks, and a block
            # index is stored in a "H" (unsigned short) register.
            message, sender = cluster_sock.recvfrom(struct.calcsize("H"))

            # However, sometimes peers only want to go. In this case,
            # they send a UDP datagram to the splitter with a
            # zero-length payload.
            if len(message) == 0:
                # An empty message is a goodbye message.
                try:
                    peer_list.remove(sender)
                    print sender, 'has left the cluster'
                except:
                    # Received a googbye message from a peer which is
                    # not in the list of peers.
                    pass
            else:
                # The sender of the packet complains, and the packet
                # comes with the index of a lost (non-received)
                # block. In this situation, the splitter counts the
                # number of times a peer has not achieved to send a
                # block to other peers. If this number exceeds a
                # threshold, the unsupportive peer is expelled from
                # the cluster. Moreover, if we receive too much
                # complains from the same peer, the problem counld be
                # in that peer and it will be expelled from the
                # cluster.
                lost_block = struct.unpack("!H",message)[0]
                try:
                    destination = destination_of_block[lost_block]
                    print sender, \
                        'complains about lost block', lost_block, \
                        'sent to', destination
                    unreliability[destination] += 1
                    complains[sender] += 1
                    if unreliability[destination] > len(peer_list):
                        print 'Too much complains about unsupportive peer', \
                            destination
                        peer_list.remove(destination)
                        del unreliability[destination]
                        del complains[destination]
                    if complains[sender] > 1000:
                        print 'Too much complains of a peevish peer', \
                            sender
                        peer_list.remove(sender)
                        del complains[sender]
                        del unreliability[sender]
                except:
                    # The unsupportive peer does not exit.
                    pass
            # }}}

    # }}}
listen_to_the_cluster().start()

block_size = 1024
block_number = 0
kbps = 0
class compute_kbps(Thread):
    # {{{

    def __init__(self):
        Thread.__init__(self)

    def run(self):
        global kbps
        last_block_number = 0
        while main_alive:
            kbps = (block_number - last_block_number) * 1024.0/1000 * 8
            last_block_number = block_number
            time.sleep(1)

    # }}}
compute_kbps().start()

source = (source_hostname, source_port)
source_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
print source_sock.getsockname(), 'Connecting to the source ', source, '...',

source_sock.connect(source)

print source_sock.getsockname(), 'Connected to ', source, '!'

channel='480.ogg'
#channel='134.ogg'
GET_message = 'GET /' + channel + ' HTTP/1.1\r\n'
GET_message += '\r\n'
source_sock.sendall(GET_message)

peer_index = 0
block_format_string = "H"+str(block_size)+"s" # "H1024s

# This is the main loop of the splitter
while True:
    try:
        # Receive data from the source
        def receive_next_block():
            global source_sock
            block = source_sock.recv(block_size)
            prev_block_size = 0
            while len(block) < block_size:
                if len(block) == prev_block_size:
                    print '\b!',
                    sys.stdout.flush()
                    time.sleep(1)
                    source_sock.close()
                    source_sock = socket.socket(socket.AF_INET,
                                                socket.SOCK_STREAM)
                    source_sock.connect(source)
                    source_sock.sendall(GET_message)
                prev_block_size = len(block)
                block += source_sock.recv(block_size - len(block))
            return block
        block = receive_next_block()
        block_number = (block_number + 1) % 65536

        # Send the block.
        peer = peer_list[peer_index]
        message = struct.pack(block_format_string,
                              socket.htons(block_number),
                              block)
        cluster_sock.sendto(message, peer)
        peer_index = (peer_index + 1) % len(peer_list)

        # Decrement (dividing by 2) unreliability and complains after
        # every 256 sent blocks.
        if (block_number % 256) == 0:
            for i in unreliability:
                unreliability[i] /= 2
            for i in complains:
                complains[i] /= 2

        print '\r', block_number, '->', peer, '('+str(kbps)+' kbps)',
        sys.stdout.flush()

    except KeyboardInterrupt:
        print 'Keyboard interrupt detected ... Exiting!'

        # Say to the daemon threads that the work has been finished,
        main_alive = False

        # Wake up the "listen_to_the_cluster" daemon, which is waiting
        # in a cluster_sock.recvfrom(...).
        cluster_sock.sendto('',('127.0.0.1',listening_port))

        # Wake up the "handle_arrivals" daemon, which is waiting in a
        # peer_connection_sock.accept().
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('127.0.0.1',listening_port))

        # Breaks this thread and returns to the parent process (usually,
        # the shell).
        break
