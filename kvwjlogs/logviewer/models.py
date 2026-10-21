# This is an auto-generated Django model module.
# You'll have to do the following manually to clean this up:
#   * Rearrange models' order
#   * Make sure each model has one field with primary_key=True
#   * Make sure each ForeignKey and OneToOneField has `on_delete` set to the desired behavior
#   * Remove `managed = False` lines if you wish to allow Django to create, modify, and delete the table
# Feel free to rename the models, but don't rename db_table values or field names.
from django.db import models

class Year(models.Model):
    year = models.IntegerField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'years'

    def __str__(self):
        return str(self.year)

class Category(models.Model):
    category = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'categories'

    def __str__(self):
        return self.category or ""

class Artist(models.Model):
    artist_name = models.TextField(blank=True, null=True)
    artist_display_name = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'artists'

    @property
    def display_name(self):
        return self.artist_display_name or self.artist_name or ""

class Track(models.Model):
    track_name = models.TextField(blank=True, null=True)
    track_display_name = models.TextField(blank=True, null=True)
    filename = models.TextField(blank=True, null=True)
    artistid = models.ForeignKey(Artist, on_delete=models.DO_NOTHING, db_column='artistid', blank=True, null=True)
    categoryid = models.ForeignKey(Category, on_delete=models.DO_NOTHING, db_column='categoryid')
    yearid = models.ForeignKey(Year, on_delete=models.DO_NOTHING, db_column='yearid', blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'tracks'

    @property
    def display_name(self):
        return self.track_display_name or self.track_name or ""

class Playback(models.Model):
    trackid = models.ForeignKey(Track, on_delete=models.DO_NOTHING, db_column='trackid')
    date = models.TextField(blank=True, null=True)
    timestamp = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'playback'
