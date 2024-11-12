from django.contrib.auth.models import User
from django.db import IntegrityError
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

import json


@method_decorator(csrf_exempt, name='dispatch')
class Signup(View):
    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body)
            if data['password'] == data['password_repeat']:
                user = User.objects.create_user(
                    username=data['username'], password=data['password'])
                user.save()
                return JsonResponse({'message': '가입 완료'}, status=201)
            else:
                return JsonResponse({'message': '비밀번호가 일치하지 않습니다.'}, status=400)
        except IntegrityError:
            return JsonResponse({'message': '이미 존재하는 이름입니다.'}, status=400)
        except KeyError:
            return JsonResponse({'message': 'Invalid data'}, status=400)


class Check(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        return Response({'message': '로그인 되어 있습니다!'}, status=status.HTTP_201_CREATED)
