from flask_wtf import FlaskForm
from wtforms import StringField, FileField, BooleanField
from wtforms.validators import DataRequired

class UploadAudioForm(FlaskForm):
    title = StringField("Interview title", validators=[DataRequired()])
    company_domain = StringField("Company/domain (optional)")
    is_finnish = BooleanField("Finnish audio (translate to English)")
    audio = FileField("Audio file", validators=[DataRequired()])

class UploadDocForm(FlaskForm):
    title = StringField("Document title", validators=[DataRequired()])
    is_finnish = BooleanField("Finnish document (translate to English)")
    doc = FileField("Supporting document", validators=[DataRequired()])
