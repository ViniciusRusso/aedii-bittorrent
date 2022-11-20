import socket as skt
import pickle as pk
from message import Message
import message
import sys

def get_node_id(addr: tuple[str, int]) -> int:
    ip1, ip2, ip3, ip4 = addr[0].split('.')
    addr_conv = (int(ip1), int(ip2), int(ip3), int(ip4), addr[1])
    return hash(addr_conv) % sys.maxsize

def get_chunk_id(chunk: tuple[str, int]) -> int:
    filename, idx = chunk
    chunk_conv = (0, 0, 0, 0, 0, 0, 0, 0, # Aceita apenas os primeiros 32 caracteres
                  0, 0, 0, 0, 0, 0, 0, 0, idx)
    for i in range(len(chunk_conv)-1):
        if i >= len(filename):
            break
        chunk_conv[i] = ord(filename[i])
        if i+1 >= len(filename):
            break
        chunk_conv[i] |= ord(filename[i+1]) << 16
    return hash(chunk_conv) % sys.maxsize

def get_distances(target_id: int, current_id: int) -> tuple[int, int]:
    dist_direct = abs(target_id - current_id) # Distância sem passar pela origem
    if target_id > current_id: # Distância passando pela origem
        dist_warped = sys.maxsize - target_id + current_id # Sentido horário
    else:
        dist_warped = sys.maxsize + target_id - current_id # Sentido anti-horário
    return dist_direct, dist_warped

class Node:
    def __init__(self, addr: tuple):
        self.addr = addr
        self.id = get_node_id(addr)
        self.prev = None
        self.next = None
        self.alive = True
        self.dict = {} # Cada entrada deve ter o formato (chunk, idx : int, final : bool)

    # Atalhos para mandar mensagem a um nó
    def __send_ok_message(self, s):
        s.sendall(pk.dumps(message.ok_message(self.addr)))
    def __send_new_node_message(self, s, addr: tuple):
        s.sendall(pk.dumps(message.new_node_message(addr, self.addr)))
    def __send_move_in_message(self, s, prev: tuple, next: tuple):
        s.sendall(pk.dumps(message.move_in_message(prev, next, self.addr)))
    def __send_up_pair_message(self, s):
        s.sendall(pk.dumps(message.up_pair_message(self.addr)))
    def __respond_up_next_message(self, s, next: tuple):
        s.sendall(pk.dumps(message.up_next_message(next, self.addr)))
    def __respond_up_prev_message(self, s, prev: tuple):
        s.sendall(pk.dumps(message.up_prev_message(prev, self.addr)))
    def __respond_file_found(self, s, current_msg: Message):
        s.sendall(pk.dumps(message.file_found(current_msg, self.addr)))
    def __respond_file_not_found(self, s, current_msg: Message):
        s.sendall(pk.dumps(message.file_not_found(current_msg, self.addr)))
    
    def __echo(self, addr: tuple):
        with skt.socket(skt.AF_INET, skt.SOCK_STREAM) as s:
            s.connect(self.next)
            s.sendall(pk.dumps(message.echo_message(addr, self.addr)))
            response_msg_data = s.recv(1024)
            response_msg: Message = pk.loads(response_msg_data)
            assert response_msg.type == message.OK ## Útil para debug
    def echo(self):
        self.__echo(self.addr)

    def enter_dht(self, known_node: tuple):
        with skt.socket(skt.AF_INET, skt.SOCK_STREAM) as s:
            s.connect(known_node)
            self.__send_new_node_message(s, self.addr)
            response_msg_data = s.recv(1024)
            response_msg: Message = pk.loads(response_msg_data)
            assert response_msg.type == message.OK

    # TODO: Diminuir o número de casos específicos dos if's
    def __handle_message(self, msg: Message, clSocket):
        if msg.type == message.ECHO:
            ip, port = msg.content.split(':')
            addr = (ip, int(port))
            if self.addr != addr: # Se o endereço atual for o mesmo do original, a mensagem propagou pela rede toda
                self.__echo(addr)
        
        elif msg.type == message.MOVE_IN:
            prev_ip, prev_port, next_ip, next_port = msg.content.split(':')
            self.prev = (prev_ip, int(prev_port))
            self.next = (next_ip, int(next_port))
        elif msg.type == message.UP_PAIR:
            self.prev = self.next = msg.sender
        elif msg.type == message.NEW_NODE:
            host, port = msg.content.split(':')
            addr = (host, int(port)) # Endereço do autor original da mensagem
            if self.prev == None: # `self` é a raíz da DHT
                self.prev = self.next = addr
                with skt.socket(skt.AF_INET, skt.SOCK_STREAM) as s:
                    s.connect(addr) # Dizer para o novo nó como se atualizar
                    self.__send_up_pair_message(s)
                    response_msg_data = s.recv(1024)
                    response_msg: Message = pk.loads(response_msg_data)
                    assert response_msg.type == message.OK ## Útil para debug
            else: # Caso geral
                new_id = get_node_id(addr)
                if new_id == self.id:
                    self.__send_ok_message(clSocket)
                    return # TODO: Tratamento de colisões
                dist_direct, dist_warped = get_distances(new_id, self.id)
                with skt.socket(skt.AF_INET, skt.SOCK_STREAM) as s:
                    is_prev_closer = (new_id < self.id) if dist_direct <= dist_warped else (new_id > self.id)
                    if is_prev_closer:
                        if msg.sender == self.prev: # Propagação quer voltar para prev (i.e. `key_id` está entre os dois nós)
                            s.connect(addr)
                            self.__send_move_in_message(s, self.prev, self.addr)
                            self.prev = addr # Novo nó agora é predecessor do atual
                            self.__respond_up_next_message(clSocket, addr)
                        else:
                            s.connect(self.prev)
                            self.__send_new_node_message(s, addr)
                    else: # next está mais próximo
                        if msg.sender == self.next: # Propagação quer voltar para next (i.e. `key_id` está entre os dois nós)
                            s.connect(addr)
                            self.__send_move_in_message(s, self.addr, self.next) # `new_id` está entre `self.id` e `next_id`
                            self.next = addr # Novo nó agora é sucessor do atual
                            self.__respond_up_prev_message(clSocket, addr)
                        else: # Continue propagando a mensagem para frente
                            s.connect(self.next)
                            self.__send_new_node_message(s, addr)
                    response_msg_data = s.recv(1024)
                    response_msg: Message = pk.loads(response_msg_data)
                    if response_msg.type == message.UP_NEXT:
                        # substitui o nó sucessor atual pelo nó adicionado na rede
                        next_ip, next_port = response_msg.content.split(':')
                        self.next = (next_ip, int(next_port))
                    elif response_msg.type == message.UP_PREV:
                        # substitui o nó anterior atual pelo nó adicionado na rede
                        prev_ip, prev_port = response_msg.content.split(':')
                        self.prev = (prev_ip, int(prev_port))
                    else:
                        assert response_msg.type == message.OK ## Útil para debug
        elif msg.type == message.GET_FILE:
            filename, index = msg.content.split(':')
            key = (filename, int(index))
            if key in self.dict:
                # responde o clSocket com o valor
                self.__respond_file_found(clSocket, msg)
            else:
                key_id = get_chunk_id(key)
                dist_direct, dist_warped = get_distances(key_id, self.id)
                with skt.socket(skt.AF_INET, skt.SOCK_STREAM) as s:
                    is_prev_closer = (new_id < self.id) if dist_direct <= dist_warped else (new_id > self.id)
                    if is_prev_closer:
                        if msg.sender == self.prev: # Propagação quer voltar para prev (i.e. `key_id` está entre os dois nós)
                            self.__respond_file_not_found(clSocket, msg)
                            return
                        s.connect(self.prev)
                    else: # next está mais próximo
                        if msg.sender == self.next: # Propagação quer voltar para next (i.e. `key_id` está entre os dois nós)
                            self.__respond_file_not_found(clSocket, msg)
                            return
                        s.connect(self.next)
                    # repassa a mensagem para o próximo nó
                    s.sendall(pk.dumps(msg))
                    response_msg_data = s.recv(1024)
                    clSocket.sendall(response_msg_data)
            return
        
        self.__send_ok_message(clSocket)
                    

    def listen(self):
        with skt.socket(skt.AF_INET, skt.SOCK_STREAM) as s:
            s.bind(self.addr)
            s.listen()
            s.settimeout(1) # Adiciona um delay (em seg.) para ele verificar se `self.alive` é verdadeiro
            while self.alive:
                try:
                    conn, conn_addr = s.accept()
                except TimeoutError:
                    continue
                with conn:
                    while True:
                        msg_data = conn.recv(1024)
                        if not msg_data: # Finalizou a conexão
                            break
                        msg: Message = pk.loads(msg_data)
                        print(f'{msg.sender} enviou mensagem para {self.addr}')
                        self.__handle_message(msg, conn)
