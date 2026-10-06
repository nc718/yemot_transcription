import os
import ssl
import sys
import tempfile
import threading
from datetime import datetime
from flask import Flask, request
import requests
from google import genai
from google.genai import types

# הגדרת UTF-8 ל-Windows
if sys.platform == 'win32':
    import locale
    try:
        locale.setlocale(locale.LC_ALL, 'he_IL.UTF-8')
    except:
        try:
            locale.setlocale(locale.LC_ALL, 'Hebrew_Israel.1255')
        except:
            pass

# Monkey patch ל-SSL
_original_create_default_context = ssl.create_default_context

def _create_unverified_context(*args, **kwargs):
    context = _original_create_default_context(*args, **kwargs)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context

ssl.create_default_context = _create_unverified_context

app = Flask(__name__)

# הגדרות
YMOT_TOKEN = os.getenv('YMOT_TOKEN', 'YOUR_TOKEN_HERE')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', 'YOUR_GEMINI_KEY_HERE')

class YemotTranscriptionService:
    """
    שירות תמלול קבצים מימות המשיח
    """
    def __init__(self, yemot_token: str, gemini_api_key: str):
        self.yemot_token = yemot_token
        self.gemini_api_key = gemini_api_key
        self.base_url = "https://www.call2all.co.il/ym/api/"
        self.client = genai.Client(api_key=gemini_api_key)
    
    def get_latest_file_from_extension(self, extension: str) -> str:
        """
        מקבל את הקובץ האחרון משלוחה מסוימת לפי סדר כרונולוגי
        """
        url = f"{self.base_url}GetIVR2Dir"
        params = {
            'token': self.yemot_token,
            'path': f'ivr2:{extension}'
        }
        
        try:
            response = requests.post(url, data=params, verify=False)
            if response.status_code == 200:
                data = response.json()
                if data.get('responseStatus') == 'OK':
                    files = data.get('files', [])
                    if files:
                        # מיון לפי תאריך וזמן
                        files_with_dates = []
                        for f in files:
                            if 'date' in f:
                                try:
                                    # הפורמט הוא DD/MM/YYYY HH:MM
                                    dt = datetime.strptime(f['date'], '%d/%m/%Y %H:%M')
                                    files_with_dates.append((dt, f['name']))
                                except:
                                    pass
                        
                        if files_with_dates:
                            files_with_dates.sort(key=lambda x: x[0], reverse=True)
                            return files_with_dates[0][1]
        except Exception as e:
            print(f"שגיאה בקבלת רשימת קבצים: {e}")
        
        return None
    
    def download_file(self, extension: str, file_name: str) -> bytes:
        """
        מוריד קובץ מה-API של ימות
        """
        url = f"{self.base_url}DownloadFile"
        params = {
            'token': self.yemot_token,
            'path': f'ivr2:{extension}/{file_name}'
        }
        
        try:
            response = requests.post(url, data=params, verify=False)
            if response.status_code == 200:
                return response.content
            return None
        except Exception as e:
            print(f"שגיאה בהורדת קובץ: {e}")
            return None
    
    def upload_tts_file(self, extension: str, file_name: str, content: str):
        """
        מעלה קובץ TTS לשלוחה מסוימת
        """
        url = f"{self.base_url}UploadTextFile"
        params = {
            'token': self.yemot_token,
            'path': f'ivr2:{extension}/{file_name}',
            'contents': content,
            'convertAudio': '1'
        }
        
        try:
            response = requests.post(url, data=params, verify=False)
            if response.status_code == 200:
                data = response.json()
                return data.get('responseStatus') == 'OK'
            return False
        except Exception as e:
            print(f"שגיאה בהעלאת קובץ: {e}")
            return False
    
    def transcribe_audio(self, audio_file_path: str) -> str:
        """
        מתמלל קובץ אודיו ב-Gemini עם תמיכה בארמית, עברית ולשון הקודש
        """
        try:
            # העלאת הקובץ ל-Gemini
            with open(audio_file_path, 'rb') as f:
                uploaded_file = self.client.files.upload(file=f)
            
            # תמלול הקובץ
            response = self.client.models.generate_content(
                model='gemini-3.5-transcribe',
                contents=[types.Part.from_uri(
                    file_uri=uploaded_file.uri,
                    mime_type='audio/wav'
                )],
                config=types.GenerateContentConfig(
                    system_instruction="Transcribe the audio accurately. The audio may contain Aramaic, Hebrew, and/or Biblical Hebrew, possibly mixed together. Return ONLY the transcription text without any explanations, notes, or additional content."
                )
            )
            
            # חילוץ התמלול מהתגובה
            transcription = response.text.strip()
            return transcription
            
        except Exception as e:
            print(f"שגיאה בתמלול: {e}")
            return None
        finally:
            # מחיקת הקובץ המועלה מ-Gemini
            try:
                if 'uploaded_file' in locals():
                    self.client.files.delete(name=uploaded_file.name)
            except:
                pass
    
    def process_transcription(self, source_extension: str, target_extension: str):
        """
        מעבד תמלול מלא: הורדה -> תמלול -> העלאה
        """
        # קבלת הקובץ האחרון משלוחת המקור
        latest_file = self.get_latest_file_from_extension(source_extension)
        
        if not latest_file:
            return "id_list_message=no_file_found"
        
        # הורדת הקובץ
        audio_data = self.download_file(source_extension, latest_file)
        
        if not audio_data:
            return "id_list_message=download_failed"
        
        # שמירת הקובץ באופן זמני
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
            temp_file.write(audio_data)
            temp_file_path = temp_file.name
        
        try:
            # תמלול הקובץ
            transcription = self.transcribe_audio(temp_file_path)
            
            if not transcription:
                return "id_list_message=transcription_failed"
            
            # יצירת שם קובץ עם timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            tts_filename = f"transcription_{timestamp}.txt"
            
            # העלאת התמלול כקובץ TTS לשלוחת היעד
            upload_success = self.upload_tts_file(target_extension, tts_filename, transcription)
            
            if not upload_success:
                return "id_list_message=upload_failed"
            
            # הצלחה
            return "id_list_message=success"
            
        finally:
            # מחיקת הקובץ הזמני
            try:
                os.unlink(temp_file_path)
            except:
                pass

# משתנה גלובלי לשירות
service = YemotTranscriptionService(YMOT_TOKEN, GEMINI_API_KEY)

@app.route('/transcribe', methods=['POST'])
def transcribe():
    """
    נקודת קצה לתמלול - מורידה את הקובץ האחרון משלוחה 7 ומעלה את התמלול לשלוחה 8
    """
    try:
        # קבלת הקובץ האחרון משלוחה 7
        latest_file = service.get_latest_file_from_extension('7')
        
        if not latest_file:
            return "id_list_message=no_file_found"
        
        # הודעה למשתמש שהקובץ נשלח לתמלול
        # תמלול ברקע
        def process_in_background():
            service.process_transcription('7', '8')
        
        thread = threading.Thread(target=process_in_background)
        thread.daemon = True
        thread.start()
        
        return "id_list_message=file_sent_for_transcription"
        
    except Exception as e:
        print(f"שגיאה: {e}")
        return "id_list_message=error"

@app.route('/health', methods=['GET'])
def health():
    """
    בדיקת בריאות
    """
    return "OK"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
