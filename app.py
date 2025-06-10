from flask import Flask, request, jsonify
from flask_cors import CORS # Import CORS
import requests
import uuid
import random
import os
import threading
import time
from collections import deque # Do przechowywania ostatnich wiadomości

app = Flask(__name__)
CORS(app) # Dodane CORS aby frontend mógł się łączyć z backendem

# Globalny słownik do zarządzania aktywnymi wątkami spamującymi.
# Klucz: username (str), Wartość: {'thread': Thread, 'stop_event': Event, 'history': deque}
active_spammers = {}

class NGLSenderBackend:
    """
    Klasa obsługująca logikę wysyłania wiadomości do NGL.link.
    Przeznaczona do działania po stronie serwera.
    """
    def __init__(self):
        self.proxies = self.load_proxies()

    def load_proxies(self):
        """
        Ładuje serwery proxy. Preferuje zmienną środowiskową PROXY_LIST (dla Render.com),
        a następnie plik 'proxies.txt'.
        Oczekiwany format: ip:port:uzytkownik:haslo
        """
        proxy_env = os.environ.get("PROXY_LIST")
        if proxy_env:
            proxy_lines = [line.strip() for line in proxy_env.split(',') if line.strip()]
        else:
            try:
                with open("proxies.txt", "r") as f:
                    proxy_lines = [line.strip() for line in f.readlines() if line.strip()]
            except FileNotFoundError:
                print("Plik 'proxies.txt' nie znaleziony i zmienna środowiskowa PROXY_LIST nie ustawiona. Wysyłanie bez proxy.")
                return None

        formatted_proxies = []
        for line in proxy_lines:
            parts = line.split(":")
            if len(parts) == 4:
                ip, port, user, pwd = parts
                formatted_proxies.append(f"http://{user}:{pwd}@{ip}:{port}")
            else:
                print(f"Ostrzeżenie: Niepoprawny format proxy w linii: {line}. Oczekiwano ip:port:uzytkownik:haslo")
        
        return formatted_proxies if formatted_proxies else None

    def send_single_message(self, username: str, message: str, deviceId: str):
        """
        Wysyła pojedynczą wiadomość do API NGL.link.
        Zwraca pełniejsze informacje o statusie i używanym proxy.
        """
        api_url = "https://ngl.link/api/submit"
        chrome_version = random.randint(100, 125)
        user_agent = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_version}.0.0.0 Safari/537.36"

        headers = {
            "User-Agent": user_agent,
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://ngl.link"
        }
        
        data = {
            "username": username,
            "question": message,
            "deviceId": deviceId,
            "gameSlug": "",
            "referrer": ""
        }

        proxy_config = None
        used_proxy_display = "Bez proxy"
        
        if self.proxies:
            selected_proxy = random.choice(self.proxies)
            proxy_config = {"http": selected_proxy, "https": selected_proxy}
            try:
                # Wyciągnij informacje o proxy do logowania (bez hasła)
                proxy_parts = selected_proxy.replace("http://", "").split("@")
                if len(proxy_parts) == 2:
                    proxy_ip_port = proxy_parts[1]
                    used_proxy_display = f"{proxy_ip_port}" # Bez "Proxy: " dla czystszego wyświetlania na froncie
                else:
                    used_proxy_display = "Nieznane proxy"
            except:
                used_proxy_display = "Ukryte proxy (błąd parsowania)"
        
        try:
            response = requests.post(api_url, headers=headers, data=data, proxies=proxy_config, timeout=10)
            response.raise_for_status()
            
            return {
                "status": "success", 
                "message": f"Wysłano (Status: {response.status_code})",
                "proxy_used": used_proxy_display,
                "http_status": response.status_code
            }
            
        except requests.exceptions.Timeout as e:
            return {
                "status": "error", 
                "message": f"Błąd timeoutu z NGL.link: {e}", 
                "details": str(e),
                "proxy_used": used_proxy_display,
                "http_status": "Timeout"
            }
        except requests.exceptions.ConnectionError as e:
            return {
                "status": "error", 
                "message": f"Błąd połączenia z NGL.link (lub proxy): {e}", 
                "details": str(e),
                "proxy_used": used_proxy_display,
                "http_status": "Connection Error"
            }
        except requests.exceptions.HTTPError as e:
            return {
                "status": "error", 
                "message": f"Błąd HTTP z NGL.link: {e.response.status_code} - {e.response.text[:100]}...", 
                "details": str(e),
                "proxy_used": used_proxy_display,
                "http_status": e.response.status_code
            }
        except Exception as e:
            return {
                "status": "error", 
                "message": f"Wystąpił nieoczekiwany błąd serwera podczas wysyłania: {e}", 
                "details": str(e),
                "proxy_used": used_proxy_display,
                "http_status": "Unexpected Error"
            }

    def _spam_worker(self, username, message, deviceId, stop_event, history_queue, interval=1):
        """
        Funkcja uruchamiana w osobnym wątku do ciągłego spamowania.
        """
        print(f"[{username}] Rozpoczęto wątek spamujący...")
        while not stop_event.is_set():
            result = self.send_single_message(username, message, deviceId)
            
            history_entry = {
                "timestamp": time.time(), # Unix timestamp dla łatwej konwersji na froncie
                "status": result["status"],
                "message": message, # Oryginalna wiadomość, która była wysyłana
                "username": username,
                "deviceId": deviceId,
                "response_message": result.get("message", "Brak wiadomości"),
                "proxy_used": result.get("proxy_used", "N/A"),
                "http_status": result.get("http_status", "N/A")
            }
            
            # Dodaj do historii wątku
            history_queue.append(history_entry)
            
            print(f"[{username}] Wysyłanie: {history_entry['response_message']} (Proxy: {history_entry['proxy_used']})")
            
            # Poczekaj chwilę, jeśli nie ma sygnału stop
            time.sleep(interval)
        print(f"[{username}] Zatrzymano wątek spamujący.")


# Inicjalizacja instancji klasy NGLSenderBackend
ngl_sender_backend = NGLSenderBackend()

@app.route('/', methods=['GET'])
def home():
    """
    Endpoint główny - sprawdzenie czy serwer działa
    """
    return jsonify({
        "status": "online",
        "message": "NGL Sender Backend jest aktywny",
        "endpoints": {
            "send_message": "/send_ngl_message [POST]",
            "start_spam": "/start_spam [POST]",
            "stop_spam": "/stop_spam [POST]",
            "spam_status": "/spam_status [GET]"
        }
    })

@app.route('/send_ngl_message', methods=['POST']) # Usunięto OPTIONS, Flask-CORS to obsługuje
def send_message_endpoint():
    """
    Endpoint API do odbierania żądań wysłania pojedynczej wiadomości z frontendu.
    """
    if not request.is_json:
        return jsonify({"status": "error", "message": "Content-Type musi być application/json"}), 400

    data = request.get_json()
    username = data.get('username')
    message = data.get('message')
    deviceId = data.get('deviceId', str(uuid.uuid4()))

    if not username or not message:
        return jsonify({"status": "error", "message": "Brakuje nazwy użytkownika lub treści wiadomości w żądaniu."}), 400

    result = ngl_sender_backend.send_single_message(username, message, deviceId)
    return jsonify(result), 200 if result["status"] == "success" else 500

@app.route('/start_spam', methods=['POST'])
def start_spam_endpoint():
    """
    Endpoint do rozpoczęcia ciągłego spamowania.
    """
    if not request.is_json:
        return jsonify({"status": "error", "message": "Content-Type musi być application/json"}), 400

    data = request.get_json()
    username = data.get('username')
    message = data.get('message')
    deviceId = data.get('deviceId')
    interval = data.get('interval', 1) # Domyślnie 1 sekunda

    if not username or not message or not deviceId:
        return jsonify({"status": "error", "message": "Brakuje nazwy użytkownika, treści wiadomości lub Device ID."}), 400

    if username in active_spammers and active_spammers[username]['thread'].is_alive():
        return jsonify({"status": "error", "message": f"Spamowanie dla użytkownika {username} już jest aktywne."}), 409 # Conflict

    stop_event = threading.Event()
    history_queue = deque(maxlen=50) # Przechowuj do 50 ostatnich wpisów historii dla tego spamera

    thread = threading.Thread(target=ngl_sender_backend._spam_worker, 
                              args=(username, message, deviceId, stop_event, history_queue, interval))
    thread.daemon = True # Uruchom jako daemon, aby zakończył się z głównym programem
    thread.start()

    active_spammers[username] = {
        'thread': thread,
        'stop_event': stop_event,
        'history': history_queue,
        'start_time': time.time(),
        'total_sent': 0, # To będzie aktualizowane przez wątek _spam_worker
        'total_errors': 0
    }
    
    return jsonify({"status": "success", "message": f"Rozpoczęto spamowanie dla użytkownika {username}."}), 200

@app.route('/stop_spam', methods=['POST'])
def stop_spam_endpoint():
    """
    Endpoint do zatrzymania ciągłego spamowania.
    """
    if not request.is_json:
        return jsonify({"status": "error", "message": "Content-Type musi być application/json"}), 400

    data = request.get_json()
    username = data.get('username')

    if not username:
        return jsonify({"status": "error", "message": "Brakuje nazwy użytkownika."}), 400

    if username in active_spammers and active_spammers[username]['thread'].is_alive():
        active_spammers[username]['stop_event'].set() # Sygnalizuj wątkowi, aby się zatrzymał
        # Opcjonalnie: poczekaj na zakończenie wątku (thread.join()), ale może zablokować HTTP
        # active_spammers[username]['thread'].join(timeout=5)
        # if not active_spammers[username]['thread'].is_alive():
        #     del active_spammers[username]
        return jsonify({"status": "success", "message": f"Wysłano sygnał zatrzymania spamowania dla użytkownika {username}."}), 200
    else:
        return jsonify({"status": "info", "message": f"Spamowanie dla użytkownika {username} nie było aktywne."}), 200

@app.route('/spam_status/<username>', methods=['GET'])
def get_spam_status(username):
    """
    Endpoint do pobierania statusu spamowania i ostatnich wiadomości dla danego użytkownika.
    """
    spammer_info = active_spammers.get(username)
    if spammer_info and spammer_info['thread'].is_alive():
        # Oblicz statystyki z kolejki historii
        successful_sends = sum(1 for entry in spammer_info['history'] if entry['status'] == 'success')
        failed_sends = sum(1 for entry in spammer_info['history'] if entry['status'] == 'error')
        
        return jsonify({
            "status": "active",
            "message": f"Spamowanie dla użytkownika {username} jest aktywne.",
            "total_sent_in_session": len(spammer_info['history']), # Liczba wpisów w historii
            "successful_sends": successful_sends,
            "failed_sends": failed_sends,
            "last_messages": list(spammer_info['history']) # Zwróć kopię historii
        }), 200
    else:
        return jsonify({
            "status": "inactive",
            "message": f"Spamowanie dla użytkownika {username} nie jest aktywne."
        }), 200

@app.errorhandler(404)
def not_found(error):
    return jsonify({"status": "error", "message": "Endpoint nie znaleziony"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"status": "error", "message": "Wewnętrzny błąd serwera", "details": str(error)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True) # debug=True tylko do rozwoju lokalnego
