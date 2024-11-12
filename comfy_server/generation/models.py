from django.db import models

import base64

# Create your models here.


class Images(models.Model):
    name = models.CharField(max_length=100)
    bucket = models.CharField(max_length=100)

    @classmethod
    def create(cls, name, bucket):
        image = cls(name=name, bucket=bucket)
        return image
