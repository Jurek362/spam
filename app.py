from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import uuid
import random
import os

app = Flask(__name__)
CORS(app)  # Dodane CORS aby frontend mógł się łączyć z backendem

class NGLSenderBackend:
    """
    Klasa obsługująca logikę wysyłania wiadomości do NGL.link.
    Przeznaczona do działania po stronie serwera.
    """
    def __init__(self):
        # Wczytuje listę proxy z zmiennej środowiskowej PROXY_LIST lub z pliku proxies.txt
        self.proxies = self.load_proxies()

    def load_proxies(self):
        """
        Ładuje serwery proxy. Preferuje zmienną środowiskową PROXY_LIST (dla Render.com),
        a następnie plik 'proxies.txt'.
        Oczekiwany format: ip:port:uzytkownik:haslo
        """
        # Spróbuj wczytać proxy ze zmiennej środowiskowej
        proxy_env = os.environ.get("PROXY_LIST")
        if proxy_env:
            # Zakładamy, że proxy w zmiennej środowiskowej są oddzielone przecinkami
            proxy_lines = [line.strip() for line in proxy_env.split(',') if line.strip()]
        else:
            # Jeśli zmienna środowiskowa nie jest ustawiona, spróbuj wczytać z pliku
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
        """
        api_url = "https://ngl.link/api/submit"

        # Generowanie losowej wersji Chrome dla nagłówka User-Agent
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

        try:
            proxy_config = None
            if self.proxies:
                proxy_config = {"http": random.choice(self.proxies)}
            
            # Wykonanie żądania POST do NGL.link
            response = requests.post(api_url, headers=headers, data=data, proxies=proxy_config, timeout=10)
            response.raise_for_status() # Wyrzuca wyjątek HTTPError dla błędnych odpowiedzi (4xx lub 5xx)
            
            return {"status": "success", "message": f"Wiadomość wysłana pomyślnie! Status HTTP: {response.status_code}"}
            
        except requests.exceptions.RequestException as e:
            # Obsługa błędów związanych z żądaniem (np. sieć, timeouty, statusy HTTP)
            return {"status": "error", "message": f"Błąd podczas wysyłania wiadomości do NGL.link: {e}", "details": str(e)}
        except Exception as e:
            # Obsługa innych nieoczekiwanych błędów
            return {"status": "error", "message": f"Wystąpił nieoczekiwany błąd serwera: {e}", "details": str(e)}

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
            "send_message": "/send_ngl_message [POST]"
        }
    })

@app.route('/send_ngl_message', methods=['POST', 'OPTIONS'])
def send_message_endpoint():
    """
    Endpoint API do odbierania żądań wysłania wiadomości z frontendu.
    """
    # Obsługa preflight OPTIONS request (CORS)
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST')
        return response
    
    # Sprawdź, czy żądanie ma poprawny typ Content-Type (application/json)
    if not request.is_json:
        return jsonify({"status": "error", "message": "Content-Type musi być application/json"}), 400

    # Pobierz dane JSON z żądania
    data = request.get_json()
    username = data.get('username')
    message = data.get('message')
    deviceId = data.get('deviceId', str(uuid.uuid4())) # Użyj deviceId z frontendu lub wygeneruj nowe

    # Walidacja wymaganych pól
    if not username or not message:
        return jsonify({"status": "error", "message": "Brakuje nazwy użytkownika lub treści wiadomości w żądaniu."}), 400

    # Wywołaj logikę wysyłania wiadomości
    result = ngl_sender_backend.send_single_message(username, message, deviceId)
    
    # Zwróć odpowiedź do frontendu w zależności od wyniku
    if result["status"] == "success":
        return jsonify(result), 200
    else:
        # Zwróć błąd serwera dla problemów z wysyłaniem do NGL.link
        return jsonify(result), 500

@app.errorhandler(404)
def not_found(error):
    """
    Obsługa błędu 404
    """
    return jsonify({"status": "error", "message": "Endpoint nie znaleziony"}), 404

@app.errorhandler(500)
def internal_error(error):
    """
    Obsługa błędu 500
    """
    return jsonify({"status": "error", "message": "Wewnętrzny błąd serwera"}), 500

if __name__ == '__main__':
    # Uruchomienie aplikacji Flask.
    # Użyj host='0.0.0.0' i portu z zmiennej środowiskowej (dla Render.com)
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False) # debug=True tylko do rozwoju lokalnego
