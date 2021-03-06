import boto3
import requests
import re
import hashlib
from app import app
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from flask_wtf import FlaskForm, RecaptchaField, Recaptcha
from wtforms import StringField, EmailField, PasswordField, HiddenField, BooleanField
from wtforms.validators import InputRequired, Email, ValidationError, EqualTo, Length

class AniListUserValidator:
    """
    Validator for checking that provided Anilist username exists.
    """
    def __call__(self, form, field):
        username = field.data
        query = f"""\
            {{
                User(name: "{username}") {{
                    id
                }}
            }}
            """
        resp = requests.post(
            "https://graphql.anilist.co",
            json={'query': query}
        )
        if not resp.ok:
            if resp.status_code == 404:
                raise ValidationError(f"AniList user {username} does not exist.")
            else:
                app.logger.warning(
                    ("Could not verify AniList user "
                     f"due to error response code: {resp}")
                )
                raise ValidationError(
                    ("There was an issue with verifying the AniList user. "
                     "Please try again later.")
                )
        else:
            try:
                # Save user id to form for later reference, as that is what
                # will be used in the database to refer to AniList account
                form.anilist_user_id = resp.json()['data']['User']['id']
            except KeyError as e:
                app.logger.error(f"Malformed response from AniList API: {e}")
                raise ValidationError(
                    ("There was an issue with the API query to verify the "
                     "AniList user. Please let the site owner know.")
                )

class StrongPasswordValidator:
    """
    Validator for ensuring that password is strong.
    """
    def __init__(self, *, requireLength=False, minLength=10, requireChars=False,
                 requireBothCase=False, requireNums=False, requireSpecial=False):
        self.requireLength = requireLength
        self.minLength = minLength
        self.requireChars = requireChars
        self.requireBothCase = requireBothCase
        self.requireNums = requireNums
        self.requireSpecial = requireSpecial
    
    def __call__(self, _, field):
        password = field.data

        if self.requireLength and len(password) < self.minLength:
            raise ValidationError(
                f"Password must be at least {self.minLength} characters long."
            )

        if self.requireChars: 
            if not re.search('[A-z]', password):
                raise ValidationError("Password must contain alphabetical characters.")

            if self.requireBothCase and \
                (not re.search('[A-Z]', password) or
                 not re.search('[a-z]', password)):
                raise ValidationError(
                    "Password must contain both lowercase and uppercase characters."
                )
        
        if self.requireNums:
            if not re.search('\d', password):
                raise ValidationError(f"Password must contain at least one number.")

        if self.requireSpecial:
            specials = r'''[!"#$%&'()*+,-./:;<=>?@\\[\]^_`{|}-]'''
            if not re.search(specials, password):
                raise ValidationError(
                    "Password must contain at least one special character."
                )

class AccessCodeValidator:
    """
    Validator for checking if email matches access code.
    """
    def __init__(self, emailAttr):
        self.emailAttr = emailAttr

    def __call__(self, form, field):
        # Get email field
        email = getattr(form, self.emailAttr).data.encode('utf-8')
        m = hashlib.sha256()
        m.update(email)
        m.update(app.config['SECRET_KEY'].encode('utf-8'))
        m.update(email)
        answer = m.hexdigest()

        if field.data != answer:
            raise ValidationError(
                ("Access code does not match email. "
                 "Please contact the site owner for the access code.")
            )
        
class EmailUniquenessValidator():
    """
    Validator for checking if email is taken.
    """
    def __call__(self, _, field):
        email = field.data
        try:
            dynamo = boto3.resource(
                'dynamodb',
                region_name=app.config['AWS_REGION_NAME']
            )
            table = dynamo.Table(app.config['AWS_USER_DYNAMODB_TABLE'])
            dups = table.query(
                IndexName='email-index',
                Select='COUNT',
                KeyConditionExpression=Key('email').eq(email)
            )['Count']
            if dups > 0:
                raise ValidationError("This email has already been registered.")
        except ClientError as e:
            app.logger.error(
                f"Failed to check DynamoDB for existing email during registration: {e}"
            )
            raise ValidationError(
                "An issue occurred on the server. Please try again later."
            )

class LoginForm(FlaskForm):
    """
    Login form for app.
    """
    email = EmailField('Email', validators=[InputRequired(), Email()])
    password = PasswordField('Password', validators=[InputRequired()])
    recaptcha = RecaptchaField(validators=[
        Recaptcha(message=("Please verify you are not a robot by completing "
                           "the ReCaptcha."))
    ])
    remember_me = BooleanField('Remember Me')
    errors_field = HiddenField("Errors")

class RegisterForm(FlaskForm):
    """
    Register form for app.
    """
    anilist_user = StringField('AniList Username', validators=[
        InputRequired(),
        AniListUserValidator()
    ])
    email = EmailField('Email', validators=[
        InputRequired(),
        Email(check_deliverability=True),
        EmailUniquenessValidator()
    ])
    password = PasswordField('Password', validators=[
        InputRequired(),
        Length(max=60), # Bcrypt only works for up to 72 bytes
        StrongPasswordValidator(
            requireLength=True, requireChars=True,
            requireBothCase=True, requireNums=True
        )
    ])
    confirm_password = PasswordField('Confirm Password', validators=[
        InputRequired(),
        EqualTo('password', message="Passwords must match.")
    ])
    access_code = StringField('Access Code', validators=[
        InputRequired(), AccessCodeValidator('email')
    ])
    recaptcha = RecaptchaField(validators=[
        Recaptcha(message=("Please verify you are not a robot by completing "
                           "the ReCaptcha."))
    ])
    remember_me = BooleanField('Remember Me')
    errors_field = HiddenField("Errors")

class ForgotPasswordForm(FlaskForm):
    """
    Forgot password form for app.
    """
    email = EmailField('Email', validators=[
        InputRequired(),
        Email(check_deliverability=True)
    ])
    errors_field = HiddenField("Errors")

class ResetPasswordForm(FlaskForm):
    """
    Reset password form for app.
    """
    password = PasswordField(
        'New Password',
        validators=[
            InputRequired(),
            Length(max=60),
            StrongPasswordValidator(
                requireLength=True, requireChars=True,
                requireBothCase=True, requireNums=True
            )
        ]
    )
    confirm_password = PasswordField('Confirm New Password', validators=[
        InputRequired(),
        EqualTo('password', message="Passwords must match.")
    ])
    user_id_field = HiddenField("User ID")
    errors_field = HiddenField("Errors")

class ChangeEmailForm(FlaskForm):
    """
    Change email form for profile page.
    """
    email = EmailField('New Email', validators=[
        InputRequired(),
        Email(check_deliverability=True),
        EmailUniquenessValidator()
    ])
    password = PasswordField('Password', validators=[InputRequired()])
    errors_field = HiddenField("Errors")

class ChangePasswordForm(FlaskForm):
    """
    Change password form for profile page.
    """
    password = PasswordField('Old Password', validators=[InputRequired()])
    new_password = PasswordField(
        'New Password',
        validators=[
            InputRequired(),
            Length(max=60),
            StrongPasswordValidator(
                requireLength=True, requireChars=True,
                requireBothCase=True, requireNums=True
            )
        ]
    )
    confirm_new_password = PasswordField(
        'Confirm New Password',
        validators=[
            InputRequired(),
            EqualTo('new_password', message="Passwords must match.")
        ]
    )
    errors_field = HiddenField("Errors")

class ChangeAniListUsernameForm(FlaskForm):
    """
    Change AniList username form for profile page.
    """
    anilist_user = StringField('New AniList Username', validators=[
        InputRequired(),
        AniListUserValidator()
    ])