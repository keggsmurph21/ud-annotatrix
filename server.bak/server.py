'''
This is the backend for the annotatrix tool. It allows to save a project
on a server and load it when needed.
'''

import sys
from io import BytesIO
from flask import Flask
from flask import jsonify
from flask import request
from flask import redirect
from flask import send_file
from flask import send_from_directory
from flask import url_for
from flask import session
from flask_github import GitHub
import os
import uuid
from db import CorpusDB
from env import Env
from log import Logger
import json
from werkzeug.utils import secure_filename
from subprocess import Popen, PIPE
import github as git_api

env = Env(filename='.env')

PATH_TO_CORPORA = env.get('PATH_TO_CORPORA', 'corpora')
SECRET_KEY = env.get('SECRET_KEY', 'secret-key-123')
HOST = env.get('HOST', '127.0.0.1')
PORT = env.get('PORT', '5316')
PROTOCOL = env.get('PROTOCOL', 'http')
ALLOWED_EXTENSIONS = set(['txt', 'conllu', 'cg3', 'sd', 'corpus']); # file extensions the user can post to /upload
GITHUB_CLIENT_ID = '298b7a22eb8bc53567d1'
GITHUB_CLIENT_SECRET = env.get('GITHUB_CLIENT_SECRET')
if not GITHUB_CLIENT_SECRET:
    raise ValueError('Please provide a GITHUB_CLIENT_SECRET to your .env file')

logger = Logger(env=env, name='SERVER')

welcome = '''
*******************************************************************************
* NOW POINT YOUR BROWSER AT: http://{}:{}/                           *
*******************************************************************************
'''
on_success = { 'status': 'success' }
on_failure = { 'status': 'failure' }

app = Flask(__name__, static_folder='../public/')
app.config['GITHUB_CLIENT_ID'] = GITHUB_CLIENT_ID
app.config['GITHUB_CLIENT_SECRET'] = GITHUB_CLIENT_SECRET
github = GitHub(app)

if not os.path.exists(PATH_TO_CORPORA):
    logger.info('Initializing: Corpus ({})'.format(PATH_TO_CORPORA))
    os.mkdir(PATH_TO_CORPORA)


@app.route('/annotatrix/save', methods=['POST'])
def save_corpus():
    print(request.POST.get('treebank_id'))
    logger.info('{} /annotatrix/save/'.format(request.method))
    if request.form:
        logger.info('/annotatrix/save/ form: {}'.format(request.form))

        state = json.loads(request.form['state']) # parse
        db_path = treebank_path(request.form['treebank_id'])
        if os.path.exists(db_path):
            logger.debug('/annotatrix/save/ updating db at {}'.format(db_path))
        else:
            logger.warn('/annotatrix/save/ no db found at {}, creating new one'.format(db_path))

        db = CorpusDB(db_path)
        try:
            db.update_db(state)
            return jsonify(on_success)
        except ValueError as e:
            logger.error(e)

    else:
        logger.warn('/annotatrix/save/ no form received')

    return jsonify(on_failure)

'''

@app.route('/load/<treebank_id>/', defaults={'num': None})
@app.route('/load/<treebank_id>/<num>', methods=['GET'])
def load_sentence(treebank_id, num):
    logger.info(f'{request.method} /load/{treebank_id}/{num}')

    db_path = treebank_path(treebank_id)
    if not os.path.exists(db_path):
        logger.warn(f'/load no db found at {db_path}')
        return jsonify(on_failure)

    db = CorpusDB(db_path)
    try:

        if num is None:
            sents, max_sents, filename, gui, labeler = db.get_sentences()
        else:
            sents, max_sents, filename, gui, labeler = db.get_sentence(num)

        return jsonify({
            'sentences': sents,
            'max': max_sents,
            'filename': filename,
            'gui': gui,
            'labeler': labeler,
            'username': session.get('username', None)
        })

    except ValueError as e:
        logger.error(e)
        return jsonify(on_failure)


@app.route('/annotatrix/download', methods=['GET', 'POST'])
def download_corpus():
    logger.info('{} /annotatrix/download'.format(request.method))
    if request.args:
        logger.info('/annotatrix/download args: {}'.format(request.args))
        treebank_id = request.args['treebank_id'].strip('#')
        db_path = treebank_path(treebank_id)
        file_path = treebank_path(treebank_id, extension='')
        if os.path.exists(db_path):
            logger.debug('/annotatrix/download updating db at {}'.format(db_path))
            db = CorpusDB(db_path)
            corpus, corpus_name = db.get_file()
            with open(file_path, 'w') as f:
                f.write(corpus)
            logger.debug('/annotatrix/download sending file {}'.format(file_path))
            return send_file(file_path, as_attachment=True, attachment_filename=corpus_name)
        else:
            logger.warn('/annotatrix/download no db found at {}'.format(db_path))
    else:
        logger.warn('/annotatrix/download no args received')
    return jsonify({'corpus': 'something went wrong'})


@app.route('/annotatrix/upload', methods=['GET', 'POST'])
def upload_new_corpus():
    logger.info('{} /annotatrix/upload'.format(request.method))
    if request.method == 'POST':
        try:
            logger.debug('/annotatrix/upload files: {}'.format(request.files))
            file, filename = validate_posted_file(request.files)
            contents = str(file.read(), 'utf-8')
            corpus_name = filename
            echo_process = Popen(['echo', contents], stdout=PIPE)
            parse_process = Popen([
                './scripts/cli-parser.js',
                '--save',
                '--host', HOST,
                '--port', PORT,
                '--protocol', PROTOCOL
            ], stdin=echo_process.stdout, stdout=PIPE)
            stdout, stderr = parse_process.communicate()
            if parse_process.returncode != 0:
                return jsonify({ 'error': stdout })

            treebank_id = str(stdout, 'utf-8')
            return redirect(url_for('corpus_page', treebank_id=treebank_id))
        except Exception as e:
            logger.error('/annotatrix/upload error: {}'.format(e))
            return jsonify({'error': str(e)})
    else:
        return jsonify({'error': 'unable to GET /annotatrix/upload'})


@app.route('/annotatrix/running', methods=['GET', 'POST'])
def running():
    logger.info('{} /annotatrix/running'.format(request.method))
    return jsonify({'status': 'running'})


@app.route('/annotatrix/', methods=['GET', 'POST'])
def annotatrix():
    logger.info('{} /annotatrix/'.format(request.method))
    treebank_id = str(uuid.uuid4())
    return redirect(url_for('corpus_page', treebank_id=treebank_id))


@app.route('/', methods=['GET', 'POST'])
def index():
    logger.info('{} /'.format(request.method))
    return app.send_static_file('html/welcome_page.html')


# @app.route('/<treebank_id>', methods=['GET', 'POST'])
# def index_corpus(treebank_id):
#     return redirect(url_for('corpus_page', treebank_id=treebank_id))

@app.route('/annotatrix/<treebank_id>/login')
def login(treebank_id):
    user_id = session.get('user_id', None)
    logger.info(f'login id:{user_id}, tree(route):{treebank_id}, tree(session):{session["treebank_id"]}')
    if user_id is None:
        session['treebank_id'] = treebank_id
        return github.authorize()
    else:
        logger.info('Already logged in:')
        return corpus_page(session['treebank_id'])


@app.route('/annotatrix/<treebank_id>/logout')
def logout(treebank_id):
    logger.info('logout')
    if session.get('user_id', None) is None:
        logger.info('Not logged in')
        return corpus_page(session['treebank_id'])
    else:
        db_path = treebank_path(session['treebank_id'])
        db = CorpusDB(db_path)
        db.modify_user(session['user_id'], token=None)
        session['user_id'] = None
        session['username'] = None
        logger.info('Successfully logged out')
        return corpus_page(session['treebank_id'])

@app.route('/annotatrix/<treebank_id>')
def corpus_page(treebank_id):
    logger.info('corpus page for treebank_id: {}'.format(treebank_id))
    #if '.' in treebank_id:
        #return send_from_directory('../standalone', treebank_id)
    return app.send_static_file('html/annotator.html')


@app.route('/css/<file>')
def serve_css(file):
    return app.send_static_file(f'css/{file}');


@app.route('/js/<file>')
def serve_js(file):
    return app.send_static_file(f'js/{file}');


@app.route('/fonts/<file>')
def serve_font(file):
    return app.send_static_file(f'fonts/{file}');


@app.route('/annotatrix/help.html')
def help_page():
    logger.info('help page')
    return app.send_static_file('html/help.html')

@app.route('/annotatrix/settings.html')
def settings_page():
    logger.info('settings page')
    return app.send_static_file('html/settings.html')


@app.route('/github-callback', methods=['GET', 'POST'])
@github.authorized_handler
def authorized(oauth_token):
    logger.info(f'token:{oauth_token}')
    logger.info(f'treebank_id:{session["treebank_id"]}')

    next_url = request.args.get('next') or url_for('index')
    if oauth_token is None:
        logger.error('no oauth token received')
        return redirect(next_url)

    if 'treebank_id' not in session:
        logger.error('no treebank_id in session')
        return redirect(next_url)

    if session['treebank_id'] is None:
        logger.error('session treebank_id is null')
        return redirect(next_url)

    db_path = treebank_path(session['treebank_id'])
    db = CorpusDB(db_path)
    user_id, username, token = db.get_user(token=oauth_token)
    if user_id is None:
        user_id = db.add_user(oauth_token)
    session['user_id'] = user_id
    if username is None:
        username = git_api.get_username(oauth_token)
        db.modify_user(user_id, username=username)
    if username is not None:
        session['username'] = username
    db.modify_user(user_id, token=oauth_token)

    return corpus_page(session['treebank_id'])

@github.access_token_getter
def token_getter():
    db_path = treebank_path(session['treebank_id'])
    db = CorpusDB(db_path)
    user_id, username, token = db.get_user(id=session['user_id'])
    if token is not None:
        return token





def validate_posted_file(files):
    if 'file' not in files:
        raise ValueError('no file received')

    file = files['file']
    if file.filename == '':
        raise ValueError('no file received')

    filename = secure_filename(file.filename)
    extension = filename.rsplit('.', 1)[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError(f'unable to upload file with extension "{extension}"')

    return file, filename


def treebank_path(treebank_id, extension='.db'):
    ' ''
    Provides a consistent way to get the path to a corpus from the treebank_id.
    Note that the path used by the db will have a `.db` extension, but files
    sent from the server will not.  In this case, the function should be called
    with extension=''.

    @param treebank_id
    @param extension
    @return path to corpus file (with extension)
    ' ''
    return os.path.join(PATH_TO_CORPORA, treebank_id.strip('#') + extension)


'''
if __name__ == '__main__':
    print(welcome.format(HOST, PORT))
    app.secret_key = SECRET_KEY
    app.run(debug = True, port = PORT)