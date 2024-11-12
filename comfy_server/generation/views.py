import json
import os
import random
import traceback
import urllib.parse
import urllib.request
import uuid
from datetime import timedelta

import boto3
import botocore
import requests
from botocore.config import Config
from dotenv import load_dotenv
from PIL import Image
from requests_toolbelt import MultipartEncoder
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.core.cache import cache
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from .image_fetcher import ImageFetcher
from .models import Images


@method_decorator(csrf_exempt, name='dispatch')
class GetWorkflows(APIView):
    def get(self, request, *args, **kwargs):
        try:
            workflows_dir = os.path.join(os.getcwd(), 'workflows')
            files = os.listdir(workflows_dir)
            workflow_names = [os.path.splitext(file)[0]
                              for file in files if file.endswith('.json')]
            return JsonResponse({'data': {
                'workflows': workflow_names
            }}, status=200)
        except Exception as e:
            print(e)
            return JsonResponse({'data': {'error_message': '워크플로우 목록을 가져오는데 실패했습니다.'}}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class QueuePrompt(APIView):
    def post(self, request, *args, **kwargs):
        try:
            dat = request.data
            allowed_params = {'prompt', 'negative_prompt', 'workflow', 'image'}
            received_params = set(dat.keys())
            invalid_params = received_params - allowed_params

            if invalid_params:
                return JsonResponse({
                    'data': {
                        'error_message': f'유효하지 않은 파라미터가 있습니다. 파라미터: {", ".join(list(invalid_params))}'
                    }
                }, status=400)

            prompt = dat.get('prompt')
            neg_prompt = dat.get('negative_prompt')
            workflow_name = dat.get('workflow', None)
            image_file = request.FILES.get('image')

            client_id = str(uuid.uuid4())
            image_uploaded = image_file is not None

            workflow_path = self._get_workflow_path(workflow_name, image_uploaded)
            workflow = _Helper.import_workflow(workflow_path)
            if workflow is None:
                return JsonResponse({'data': {
                    'error_message': '서버에 해당하는 워크플로우가 없습니다.'
                }}, status=404)

            if not image_uploaded:
                response = self._queue_prompt_no_image(prompt, neg_prompt, client_id, workflow)
            else:
                response = self._queue_prompt_with_image(image_file, prompt, neg_prompt, client_id, workflow)

            prompt_id = json.loads(response.text)["prompt_id"]
            cache_key = f'prompt_id_{prompt_id}'
            cache.set(cache_key, prompt_id, timeout=timedelta(hours=2).total_seconds())

            return JsonResponse({
                'data': {
                    'prompt_id': prompt_id
                }
            }, status=201)
        except Exception as e:
            print(e)
            return JsonResponse({'data': {
                'error_message': '이미지 생성 작업 예약에 실패했습니다.'
            }}, status=500)

    def _get_workflow_path(self, workflow_name, image_uploaded):
        return os.path.join(os.getcwd(), 'workflows',
                            f'{workflow_name if workflow_name else (_ConfigManager.default_image_workflow_name() if image_uploaded else _ConfigManager.default_workflow_name())}.json')

    def _queue_prompt_no_image(self, prompt, neg_prompt, client_id, workflow):
        default_prompt = _Helper.get_default_prompt(workflow, prompt, neg_prompt)
        d = {'client_id': client_id, 'prompt': default_prompt}
        return requests.post(f'http://{_ConfigManager.comfyui_server_url()}/prompt',
                             data=json.dumps(d), headers={'Content-Type': 'application/json'})

    def _queue_prompt_with_image(self, image_file, prompt, neg_prompt, client_id, workflow):
        image = Image.open(image_file)
        image_name = str(uuid.uuid4())
        image.save(f'./{image_name}.png')

        _Helper.send_image(f"./{image_name}.png", image_name, image_type="input", overwrite=False)
        os.remove(f'./{image_name}.png')

        default_prompt = _Helper.get_default_image_prompt(workflow, image_name, prompt, neg_prompt)
        d = {'client_id': client_id, 'prompt': default_prompt, 'image': image_name}
        return requests.post(f'http://{_ConfigManager.comfyui_server_url()}/prompt',
                             data=json.dumps(d), headers={'Content-Type': 'application/json'})


@method_decorator(csrf_exempt, name='dispatch')
class GetProgress(APIView):
    """
    진행률을 확인하는 뷰
    """
    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body)
            prompt_id = data.get("prompt_id")
            cache_key = f'prompt_id_{prompt_id}'
            cached_prompt_id = cache.get(cache_key)

            if not cached_prompt_id:
                return JsonResponse({'data': {
                    'error_message': '유효하지 않은 prompt_id입니다.'
                }}, status=400)

            fetcher = ImageFetcher()
            result = fetcher.get_progress(prompt_id)
            return JsonResponse({'data': {
                'progress': result
            }}, status=200)
        except Exception:
            traceback.print_exc()
            return JsonResponse({'data': {
                'error_message': '진행률을 가져오는데 실패했습니다.'
            }}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class FetchImage(APIView):
    """
    이미지 가져오기 뷰
    """
    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body)
            prompt_id = data.get("prompt_id")
            cache_key = f'prompt_id_{prompt_id}'
            cached_prompt_id = cache.get(cache_key)

            if not cached_prompt_id:
                return JsonResponse({'data': {
                    'error_message': '유효하지 않은 prompt_id입니다.'
                }}, status=400)

            fetcher = ImageFetcher()
            image_path = fetcher.fetch(prompt_id, include_previews=True)

            if image_path is None:
                return JsonResponse({'data': {
                    'error_message': '이미지가 아직 생성되지 않았습니다.'
                }}, status=400)

            obj_name = _Helper.upload_to_S3(image_path)
            return JsonResponse({'data': {
                'image_url': _Helper.get_image_s3_url(obj_name)
            }}, status=201)
        except Exception:
            traceback.print_exc()
            return JsonResponse({'data': {
                'error_message': '이미지를 가져오는데 실패했습니다.'
            }}, status=500)


class _ConfigManager:
    """환경 변수를 관리하는 정적 클래스."""
    _loaded = False
    _comfyui_server_url = None
    _default_workflow_name = None
    _default_image_workflow_name = None
    _aws_access_key = None
    _aws_secret_key = None
    _bucket = None

    @classmethod
    def load(cls):
        """환경 변수를 로드하고 클래스 속성에 저장합니다."""
        if not cls._loaded:
            load_dotenv()
            cls._comfyui_server_url = os.getenv('COMFYUI_SERVER_URL')
            cls._default_workflow_name = os.getenv('DEFAULT_WORKFLOW')
            cls._default_image_workflow_name = os.getenv('DEFAULT_IMAGE_WORKFLOW')
            cls._aws_access_key = os.getenv('AWS_ACCESS_KEY')
            cls._aws_secret_key = os.getenv('AWS_SECRET_KEY')
            cls._bucket = os.getenv('BUCKET')
            cls._loaded = True

    @classmethod
    def comfyui_server_url(cls):
        cls.load()
        return cls._comfyui_server_url

    @classmethod
    def default_workflow_name(cls):
        cls.load()
        return cls._default_workflow_name

    @classmethod
    def default_image_workflow_name(cls):
        cls.load()
        return cls._default_image_workflow_name

    @classmethod
    def aws_access_key(cls):
        cls.load()
        return cls._aws_access_key

    @classmethod
    def aws_secret_key(cls):
        cls.load()
        return cls._aws_secret_key

    @classmethod
    def bucket(cls):
        cls.load()
        return cls._bucket


class _Helper:
    @staticmethod
    def import_workflow(workflow_path):
        try:
            with open(workflow_path, 'r') as file:
                return json.dumps(json.load(file))
        except Exception as e:
            print(f"워크플로우 오류: {e}")
            return None

    @staticmethod
    def get_default_prompt(workflow, prompt, negative_prompt):
        default_prompt = json.loads(workflow)
        id_to_class_type = {id: details['class_type']
                            for id, details in default_prompt.items()}
        k_sampler = next(
            key for key, value in id_to_class_type.items() if value == 'KSampler')

        default_prompt[k_sampler]['inputs']['seed'] = random.randint(
            10 ** 14, 10 ** 15 - 1)
        default_prompt[default_prompt[k_sampler]['inputs']['positive']
        [0]]['inputs']['text'] = prompt
        default_prompt[default_prompt[k_sampler]['inputs']['negative']
        [0]]['inputs']['text'] = negative_prompt
        return default_prompt

    @staticmethod
    def get_default_image_prompt(workflow, image_name, prompt, negative_prompt):
        default_prompt = json.loads(workflow)
        id_to_class_type = {id: details['class_type']
                            for id, details in default_prompt.items()}
        k_sampler = next(
            key for key, value in id_to_class_type.items() if value == 'KSampler')

        default_prompt[k_sampler]['inputs']['seed'] = random.randint(
            10 ** 14, 10 ** 15 - 1)
        default_prompt[default_prompt[k_sampler]['inputs']['positive']
        [0]]['inputs']['text'] = prompt
        default_prompt[default_prompt[k_sampler]['inputs']['negative']
        [0]]['inputs']['text'] = negative_prompt
        for k, v in default_prompt.items():
            if v["class_type"] == "LoadImage":
                v["inputs"]["image"] = image_name
                print(v)

        return default_prompt

    @staticmethod
    def upload_to_S3(image_path):
        s3_client = boto3.client('s3',
                                 region_name='ap-northeast-2',
                                 aws_access_key_id=_ConfigManager.aws_access_key(),
                                 aws_secret_access_key=_ConfigManager.aws_secret_key())

        obj_name = os.path.basename(image_path)
        s3_client.upload_file(
            f'{image_path}', _ConfigManager.bucket(), obj_name)

        image = Images(name=obj_name, bucket=_ConfigManager.bucket())
        image.save()
        return obj_name

    @staticmethod
    def get_image_s3_url(obj_name):
        config = Config(signature_version=botocore.UNSIGNED)
        config.signature_version = botocore.UNSIGNED
        url = boto3.client('s3', config=config).generate_presigned_url(
            'get_object', ExpiresIn=600, Params={'Bucket': _ConfigManager.bucket(), 'Key': obj_name})
        return url

    @staticmethod
    def send_image(input_path, name, image_type="input", overwrite=False):
        with open(input_path, 'rb') as file:
            data = MultipartEncoder(fields={
                'image': (name, file, 'image/png'),
                'type': image_type,
                'overwrite': str(overwrite).lower()
            })
            req = urllib.request.Request(f"http://{_ConfigManager.comfyui_server_url()}/upload/image",
                                         data=data,
                                         headers={'Content-Type': data.content_type})
            with urllib.request.urlopen(req) as response:
                return response.read()
