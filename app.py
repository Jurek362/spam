from flask import Flask, request, jsonify
from flask_cors import CORS # Import CORS
import requests
import uuid
import random
import os
import threading
import time
from collections import deque # Do przechowywania ostatnich wiadomości
import re # Importujemy moduł do wyrażeń regularnych

# Firebase imports
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app) # Dodane CORS, aby frontend mógł łączyć się z backendem

# Wzorzec do walidacji formatu UUID
UUID_PATTERN = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$', re.IGNORECASE)

# Inicjalizacja Firebase Admin SDK
firestore_db = None
try:
    # Spróbuj zainicjować Firebase Admin SDK.
    # W środowiskach Google Cloud (App Engine, Cloud Run) często działa automatycznie.
    # W innych środowiskach (np. Render.com), upewnij się, że zmienna środowiskowa
    # GOOGLE_APPLICATION_CREDENTIALS wskazuje na plik klucza konta serwisowego.
    if not firebase_admin._apps: # Uniknij ponownej inicjalizacji w testach
        firebase_admin.initialize_app()
    firestore_db = firestore.client()
    print("Firebase Admin SDK zainicjowane pomyślnie.")
except Exception as e:
    print(f"Błąd inicjalizacji Firebase Admin SDK: {e}. Funkcje Firestore mogą nie działać.")
    firestore_db = None # Ustaw na None, jeśli inicjalizacja się nie powiedzie

# app_id pobrane ze zmiennej środowiskowej, która powinna być ustawiona podczas wdrożenia.
# Jest to konieczne do budowania ścieżki kolekcji w Firestore.
app_id = os.environ.get('APP_ID', 'default-canvas-app-id')

# Definicja ścieżki kolekcji Firestore dla kluczy aktywacyjnych
# Zgodnie z zasadami: /artifacts/{appId}/public/data/{your_collection_name}
KEYS_COLLECTION_PATH = f"artifacts/{app_id}/public/data/activation_keys"
print(f"Ścieżka kolekcji kluczy Firestore: {KEYS_COLLECTION_PATH}")


# Globalny słownik do zarządzania aktywnymi sesjami spamującymi użytkowników.
# Klucz: username (str), Wartość: {'stop_event': Event, 'proxy_threads': list, 'history': deque, 'request_counter': int}
active_spammers = {}
# Używamy zamka do bezpiecznego dostępu do history_queue i request_counter
history_lock = threading.Lock()

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
                    proxy_lines = [line.strip() for f.readlines() if line.strip()]
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

    def _format_proxies_from_input(self, raw_proxy_lines: list[str]):
        """
        Formatuje listę surowych stringów proxy (ip:port:user:pwd) do formatu HTTP(S) z uwierzytelnieniem.
        """
        formatted_proxies = []
        for line in raw_proxy_lines:
            parts = line.split(":")
            if len(parts) == 4:
                ip, port, user, pwd = parts
                formatted_proxies.append(f"http://{user}:{pwd}@{ip}:{port}")
            else:
                print(f"Ostrzeżenie: Niepoprawny format proxy w linii: {line}. Oczekiwano ip:port:uzytkownik:haslo")
        return formatted_proxies if formatted_proxies else None


    def send_single_message_via_proxy(self, username: str, message: str, deviceId: str, proxy_url: str):
        """
        Wysyła pojedynczą wiadomość do API NGL.link za pośrednictwem określonego proxy.
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
            "deviceId": deviceId, # Użyj deviceId przesłanego z frontendu
            "gameSlug": "",
            "referrer": ""
        }

        proxy_config = None
        used_proxy_display = "Bez proxy"
        
        if proxy_url: # Sprawdź, czy proxy_url nie jest None
            selected_proxy = proxy_url # Użyj przekazanego proxy_url
            proxy_config = {"http": selected_proxy, "https": selected_proxy}
            try:
                # Wyciągnij informacje o proxy do logowania (bez hasła)
                proxy_parts = selected_proxy.replace("http://", "").split("@")
                if len(proxy_parts) == 2:
                    proxy_ip_port = proxy_parts[1]
                    used_proxy_display = f"{proxy_ip_port}" # Bez "Proxy: " dla czystszego wyświetlania na froncie
                else:
                    used_proxy_display = selected_proxy # Jeśli format nie pasuje, wyświetl cały URL
            except:
                used_proxy_display = "Ukryte proxy (błąd parsowania)"
        # else: # Jeśli brak proxy, proxy_config jest None (domyślnie)

        try:
            response = requests.post(api_url, headers=headers, data=data, proxies=proxy_config, timeout=10)
            response.raise_for_status()
            
            return {
                "status": "success", 
                "message": "Wysłano!", # Uproszczony komunikat dla frontendu
                "proxy_used": used_proxy_display,
                "http_status": response.status_code
            }
            
        except requests.exceptions.Timeout as e:
            return {
                "status": "error", 
                "message": "Błąd timeoutu!", 
                "details": str(e),
                "proxy_used": used_proxy_display,
                "http_status": "Timeout"
            }
        except requests.exceptions.ConnectionError as e:
            return {
                "status": "error", 
                "message": "Błąd połączenia!", 
                "details": str(e),
                "proxy_used": used_proxy_display,
                "http_status": "Connection Error"
            }
        except requests.exceptions.HTTPError as e:
            return {
                "status": "error", 
                "message": f"Błąd HTTP ({e.response.status_code})!", 
                "details": str(e),
                "proxy_used": used_proxy_display,
                "http_status": e.response.status_code
            }
        except Exception as e:
            return {
                "status": "error", 
                "message": "Nieoczekiwany błąd!", 
                "details": str(e),
                "proxy_used": used_proxy_display,
                "http_status": "Unexpected Error"
            }

    def _single_proxy_spam_task(self, username: str, message: str, base_deviceId: str, proxy_url: str, stop_event: threading.Event, history_queue: deque, request_counter_lock: threading.Lock, interval: float):
        """
        Funkcja uruchamiana w osobnym wątku dla każdego proxy, do ciągłego spamowania.
        """
        print(f"[{username}] Rozpoczęto wątek dla proxy: {proxy_url}")
        while not stop_event.is_set():
            current_device_id = f"{base_deviceId}-{str(uuid.uuid4())[:8]}" # Unikalny ID dla każdej wiadomości
            result = self.send_single_message_via_proxy(username, message, current_device_id, proxy_url)
            
            with request_counter_lock:
                # Zwiększ licznik globalny dla użytkownika
                active_spammers[username]['request_counter'] += 1
                request_num = active_spammers[username]['request_counter']

            history_entry = {
                "request_num": request_num, # Numer żądania w sesji
                "timestamp": time.time(), # Unix timestamp
                "status": result["status"],
                "message": message, # Oryginalna wiadomość, która była wysyłana
                "username": username,
                "deviceId": current_device_id,
                "response_message": result["message"],
                "proxy_used": result["proxy_used"],
                "http_status": result["http_status"],
                "details": result.get("details", "") # Dodatkowe detale błędu
            }
            
            with history_lock: # Zabezpiecz dostęp do history_queue
                history_queue.append(history_entry)
            
            # Format wyjścia w konsoli: [+] Request #21 | Wysłano! | Proxy: http://45.115.136.32:8080
            status_char = "[+]" if result['status'] == 'success' else "[-]"
            print(f"{status_char} Request #{request_num} | {result['message']} | Proxy: {result['proxy_used']}")
            
            # Poczekaj chwilę, jeśli nie ma sygnału stop
            time.sleep(interval)
        print(f"[{username}] Zatrzymano wątek dla proxy: {proxy_url}")


    def _spam_orchestrator(self, username: str, message: str, base_deviceId: str, stop_event: threading.Event, history_queue: deque, request_counter_lock: threading.Lock, interval: float, custom_proxies: list[str] | None = None):
        """
        Orchestrator tworzy i zarządza wątkami spamującymi dla każdego proxy.
        Używa custom_proxies, jeśli są dostarczone i niepuste, w przeciwnym razie używa domyślnych.
        """
        proxies_to_use = []
        if custom_proxies:
            formatted_custom_proxies = self._format_proxies_from_input(custom_proxies)
            if formatted_custom_proxies:
                proxies_to_use = formatted_custom_proxies
            else:
                print(f"[{username}] Niestandardowe proxy są puste lub niepoprawnie sformatowane. Używam domyślnych proxy.")
                proxies_to_use = self.proxies
        else:
            proxies_to_use = self.proxies

        if not proxies_to_use:
            print(f"[{username}] Brak proxy. Spamowanie niemożliwe.")
            history_queue.append({
                "request_num": 0, "timestamp": time.time(), "status": "error",
                "message": message, "username": username, "deviceId": base_deviceId,
                "response_message": "Brak skonfigurowanych proxy. Spamowanie niemożliwe.",
                "proxy_used": "N/A", "http_status": "No Proxies", "details": ""
            })
            stop_event.set() # Natychmiast zatrzymaj, jeśli nie ma proxy
            return

        proxy_threads = []
        for proxy_url in proxies_to_use:
            thread = threading.Thread(target=self._single_proxy_spam_task, 
                                      args=(username, message, base_deviceId, proxy_url, stop_event, history_queue, request_counter_lock, interval))
            thread.daemon = True # Uruchom jako daemon, aby zakończył się z głównym programem
            proxy_threads.append(thread)
            thread.start()
        
        # Zapisz wątki do globalnego słownika
        with history_lock: # Zabezpiecz dostęp do active_spammers
            active_spammers[username]['proxy_threads'] = proxy_threads

        # Główny wątek orchestratora po prostu czeka na sygnał stop
        stop_event.wait() # Czekaj, aż stop_event zostanie ustawiony

        # Można dodać krótki sleep, aby dać wątkom czas na zatrzymanie
        # for t in proxy_threads:
        #    t.join(timeout=1)
        print(f"[{username}] Orchestrator zakończył działanie.")


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
            "spam_status": "/spam_status/<username> [GET]",
            "register_activation_key": "/register_activation_key [POST]", # Zmieniona nazwa
            "activate": "/activate [POST]" 
        }
    })

@app.route('/register_activation_key', methods=['POST'])
def register_activation_key_endpoint():
    """
    Endpoint do generowania i zapisywania nowego klucza aktywacyjnego w Firestore z datą wygaśnięcia.
    """
    if not firestore_db:
        return jsonify({"status": "error", "message": "Błąd serwera: Firestore nie jest zainicjowany."}), 500
    
    if not request.is_json:
        return jsonify({"status": "error", "message": "Content-Type musi być application/json"}), 400

    data = request.get_json()
    new_key = data.get('key')

    if not new_key:
        return jsonify({"status": "error", "message": "Brakuje klucza aktywacyjnego."}), 400
    
    if not UUID_PATTERN.match(new_key):
        return jsonify({"status": "error", "message": "Nieprawidłowy format klucza aktywacyjnego. Oczekiwano formatu UUID."}), 400

    try:
        key_doc_ref = firestore_db.collection(KEYS_COLLECTION_PATH).document(new_key)
        
        # Sprawdź, czy klucz już istnieje
        doc = key_doc_ref.get()
        if doc.exists:
            return jsonify({"status": "error", "message": "Ten klucz aktywacyjny już istnieje. Proszę wygenerować nowy."}), 409 # Conflict
        
        # Oblicz datę wygaśnięcia (7 dni od teraz)
        generated_at = datetime.now()
        expires_at = generated_at + timedelta(days=7)
        
        key_doc_ref.set({
            "generated_at": generated_at,
            "expires_at": expires_at,
            "is_active": True # Można dodać flagę do ręcznej dezaktywacji
        })
        print(f"Zarejestrowano nowy klucz w Firestore: {new_key}, wygasa: {expires_at}")
        return jsonify({"status": "success", "message": "Klucz aktywacyjny został pomyślnie zarejestrowany."}), 200
    except Exception as e:
        print(f"Błąd podczas rejestracji klucza w Firestore: {e}")
        return jsonify({"status": "error", "message": f"Wystąpił błąd serwera podczas rejestracji klucza: {e}"}), 500


@app.route('/activate', methods=['POST'])
def activate_endpoint():
    """
    Endpoint do weryfikacji klucza aktywacyjnego z Firestore.
    """
    if not firestore_db:
        return jsonify({"status": "error", "message": "Błąd serwera: Firestore nie jest zainicjowany."}), 500

    if not request.is_json:
        return jsonify({"status": "error", "message": "Content-Type musi być application/json"}), 400

    data = request.get_json()
    entered_key = data.get('key')

    if not entered_key:
        return jsonify({"status": "error", "message": "Brakuje klucza aktywacyjnego."}), 400
    
    if not UUID_PATTERN.match(entered_key):
        return jsonify({"status": "error", "message": "Nieprawidłowy format klucza aktywacyjnego. Oczekiwano formatu UUID."}), 400

    try:
        key_doc_ref = firestore_db.collection(KEYS_COLLECTION_PATH).document(entered_key)
        doc = key_doc_ref.get()

        if not doc.exists:
            return jsonify({"status": "error", "message": "Klucz aktywacyjny nie istnieje."}), 401 # Unauthorized
        
        key_data = doc.to_dict()
        expires_at_timestamp = key_data.get('expires_at')
        is_active = key_data.get('is_active', True) # Domyślnie aktywny, jeśli flaga nie istnieje

        if not is_active:
            return jsonify({"status": "error", "message": "Klucz aktywacyjny został dezaktywowany."}), 401

        # Firestore Timestamp objects convert to datetime objects in Python
        if expires_at_timestamp and datetime.now() < expires_at_timestamp:
            return jsonify({"status": "success", "message": "Klucz aktywacyjny jest poprawny!"}), 200
        else:
            print(f"Klucz {entered_key} wygasł lub jest niepoprawny. Wygasł o: {expires_at_timestamp}")
            return jsonify({"status": "error", "message": "Klucz aktywacyjny wygasł."}), 401
            
    except Exception as e:
        print(f"Błąd podczas weryfikacji klucza w Firestore: {e}")
        return jsonify({"status": "error", "message": f"Wystąpił błąd serwera podczas weryfikacji klucza: {e}"}), 500


@app.route('/send_ngl_message', methods=['POST'])
def send_message_endpoint():
    """
    Endpoint API do odbierania żądań wysłania pojedynczej wiadomości z frontendu.
    (Zachowany dla kompatybilności, choć spamowanie używa /start_spam)
    """
    if not request.is_json:
        return jsonify({"status": "error", "message": "Content-Type musi być application/json"}), 400

    data = request.get_json()
    username = data.get('username')
    message = data.get('message')
    # Generowanie unikalnego deviceId dla pojedynczego żądania
    deviceId = str(uuid.uuid4())

    if not username or not message:
        return jsonify({"status": "error", "message": "Brakuje nazwy użytkownika lub treści wiadomości w żądaniu."}), 400

    # Dla pojedynczej wiadomości losowo wybierz proxy, jeśli są dostępne
    selected_proxy_url = random.choice(ngl_sender_backend.proxies) if ngl_sender_backend.proxies else None
    result = ngl_sender_backend.send_single_message_via_proxy(username, message, deviceId, selected_proxy_url)
    return jsonify(result), 200 if result["status"] == "success" else 500

@app.route('/start_spam', methods=['POST'])
def start_spam_endpoint():
    """
    Endpoint do rozpoczęcia ciągłego spamowania.
    Klucz aktywacyjny jest sprawdzany przez frontend przy wejściu do aplikacji.
    """
    if not request.is_json:
        return jsonify({"status": "error", "message": "Content-Type musi być application/json"}), 400

    data = request.get_json()
    username = data.get('username')
    message = data.get('message')
    base_deviceId = data.get('deviceId') # To jest ID sesji z frontendu
    interval = float(data.get('interval', 1)) # Domyślnie 1 sekunda, konwersja na float
    raw_proxy_list_from_input = data.get('proxy_list', None) # Opcjonalna lista proxy z frontendu
    # Klucz aktywacyjny NIE jest już sprawdzany tutaj, tylko przez endpoint /activate i frontend

    if not username or not message or not base_deviceId:
        return jsonify({"status": "error", "message": "Brakuje nazwy użytkownika, treści wiadomości lub Device ID."}), 400

    with history_lock: # Zabezpiecz dostęp do active_spammers
        if username in active_spammers and active_spammers[username]['main_thread'].is_alive():
            return jsonify({"status": "error", "message": f"Spamowanie dla użytkownika {username} już jest aktywne."}), 409 # Conflict

        stop_event = threading.Event()
        history_queue = deque(maxlen=200) # Przechowuj więcej wpisów historii
        request_counter_lock = threading.Lock() # Zamek dla licznika żądań

        # Główny wątek orchestratora
        main_thread = threading.Thread(target=ngl_sender_backend._spam_orchestrator, 
                                       args=(username, message, base_deviceId, stop_event, history_queue, request_counter_lock, interval, raw_proxy_list_from_input))
        main_thread.daemon = True
        main_thread.start()

        active_spammers[username] = {
            'main_thread': main_thread,
            'stop_event': stop_event,
            'history': history_queue,
            'request_counter': 0, # Licznik dla wszystkich żądań w sesji
            'request_counter_lock': request_counter_lock, # Zamek dla licznika
            'proxy_threads': [] # Lista wątków dla poszczególnych proxy
        }
    
    return jsonify({"status": "success", "message": f"Rozpoczęto spamowanie dla użytkownika {username} przez wszystkie proxy."}), 200

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

    with history_lock: # Zabezpiecz dostęp do active_spammers
        if username in active_spammers and active_spammers[username]['main_thread'].is_alive():
            active_spammers[username]['stop_event'].set()
            # Opcjonalnie: poczekaj na zakończenie wątku (thread.join()), ale może zablokować HTTP
            # active_spammers[username]['main_thread'].join(timeout=5)
            # del active_spanners[username] # Usuń po całkowitym zatrzymaniu
            return jsonify({"status": "success", "message": f"Wysłano sygnał zatrzymania spamowania dla użytkownika {username}. Proszę poczekać na zakończenie wątków."}), 200
        else:
            return jsonify({"status": "info", "message": f"Spamowanie dla użytkownika {username} nie było aktywne."}), 200

@app.route('/spam_status/<username>', methods=['GET'])
def get_spam_status(username):
    """
    Endpoint do pobierania statusu spamowania i ostatnich wiadomości dla danego użytkownika.
    """
    with history_lock: # Zabezpiecz dostęp do active_spammers
        spammer_info = active_spammers.get(username)

        if spammer_info and spammer_info['main_thread'].is_alive():
            # Sprawdź, czy którykolwiek z wątków proxy jest nadal aktywny
            any_proxy_thread_active = any(t.is_alive() for t in spammer_info.get('proxy_threads', []))
            
            # W obliczu darmowego tieru Render.com, spammer_info['main_thread'].is_alive() może być wystarczające
            # lub możemy polegać na tym, czy stop_event został ustawiony
            is_active = not spammer_info['stop_event'].is_set()

            # Oblicz statystyki z kolejki historii
            current_history = list(spammer_info['history'])
            successful_sends = sum(1 for entry in current_history if entry['status'] == 'success')
            failed_sends = sum(1 for entry in current_history if entry['status'] == 'error')
            
            return jsonify({
                "status": "active" if is_active else "inactive",
                "message": f"Spamowanie dla użytkownika {username} jest {'aktywne' if is_active else 'nieaktywne'}.",
                "total_sent_in_session": spammer_info['request_counter'], # Całkowita liczba wysłanych żądań
                "successful_sends": successful_sends,
                "failed_sends": failed_sends,
                "last_messages": current_history # Zwróć kopię historii
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
    app.run(host='0.0.0.0', port=port, debug=False) # debug=True tylko do rozwoju lokalnego
