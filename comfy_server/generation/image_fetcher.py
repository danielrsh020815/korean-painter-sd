import io
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime

from dotenv import load_dotenv
from PIL import Image

load_dotenv()


class ImageFetcher:
    def __init__(self):
        self.comfyui_server_addr = os.environ.get('COMFYUI_SERVER_URL')
        self.output_image_path = os.environ.get('OUTPUT_IMAGE_PATH')

    def _fetch_history(self, prompt_id):
        with urllib.request.urlopen(f"http://{self.comfyui_server_addr}/history/{prompt_id}") as response:
            return json.loads(response.read())

    def _retrieve_image(self, filename, subfolder, folder_type):
        params = urllib.parse.urlencode(
            {"filename": filename, "subfolder": subfolder, "type": folder_type})
        with urllib.request.urlopen(f"http://{self.comfyui_server_addr}/view?{params}") as response:
            return response.read()

    def _get_image_data(self, prompt_id, include_previews=False):
        result = None
        history = self._fetch_history(prompt_id)
        if prompt_id not in history:
            return None

        for node_id, node_output in history[prompt_id]['outputs'].items():
            if 'images' in node_output:
                for image in node_output['images']:
                    d = {
                        'file_name': f"image_{datetime.now().strftime('%Y%m%d%H%M%S')}.png",
                        'type': image['type']
                    }
                    if (include_previews and image['type'] == 'temp') or image['type'] == 'output':
                        d['image_data'] = self._retrieve_image(
                            image['filename'], image['subfolder'], image['type'])
                    result = d
                    break
                if result:
                    break
        return result

    def _store_image(self, image_data, output_path, include_previews):
        try:
            file_name = image_data['file_name']
            image_type = image_data['type']

            directory = os.path.join(
                output_path, 'temp') if image_type == 'temp' and include_previews else output_path
            os.makedirs(directory, exist_ok=True)

            image = Image.open(io.BytesIO(image_data['image_data']))
            save_path = os.path.join(directory, file_name)
            image.save(save_path)
            return os.path.abspath(save_path)
        except KeyError as e:
            print(f"이미지 데이터 형식 오류: {e}")
        except IOError as e:
            print(f"이미지 저장 중 IO 오류 발생: {e}")
        except Exception as e:
            print(f"이미지 저장 중 예상치 못한 오류 발생: {e}")
        return None

    def fetch(self, prompt_id, include_previews=False):
        image_data = self._get_image_data(prompt_id, include_previews)
        if image_data is None:
            return None

        return self._store_image(image_data, self.output_image_path, include_previews)

    def get_progress(self, prompt_id):
        prompt = self._fetch_history(prompt_id)
        if not prompt or prompt_id not in prompt:
            return 0.0

        prompt_data = prompt[prompt_id]

        if prompt_data['status']['completed']:
            return 100.0

        if isinstance(prompt_data['prompt'], list) and len(prompt_data['prompt']) > 1:
            total_nodes = len(prompt_data['prompt'][1])
        else:
            total_nodes = len(prompt_data.get('outputs', {}))

        if total_nodes == 0:
            return 0.0

        executed_nodes = set()
        for message in prompt_data['status'].get('messages', []):
            if message[0] in ['execution_cached', 'executing']:
                executed_nodes.update(message[1].get('nodes', []))

        progress = (len(executed_nodes) / total_nodes) * 100
        return round(progress, 2)
