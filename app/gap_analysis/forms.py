from flask_wtf import FlaskForm
from wtforms import SelectField, SelectMultipleField
from wtforms.validators import DataRequired

class NewGapReportForm(FlaskForm):
    interview_id = SelectField("Select interview", coerce=int, validators=[DataRequired()])
    support_doc_ids = SelectMultipleField("Select up to 3 supporting docs", coerce=int, validators=[DataRequired()])
