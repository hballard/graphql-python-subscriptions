from setuptools import setup, find_packages
import sys

try:
    import pypandoc
    long_description = pypandoc.convert('README.md', 'rst')
except (IOError, ImportError):
    long_description = open('README.md').read()

tests_dep = [
    'pytest', 'pytest-mock', 'fakeredis', 'graphene',
    'flask', 'flask-graphql', 'flask-sockets', 'multiprocess', 'requests'
]

if sys.version_info[0] < 3:
    tests_dep.append('subprocess32')

setup(
    name='graphql-subscriptions',
    version='0.1.9',
    author='Heath Ballard',
    author_email='heath.ballard@gmail.com',
    description=('A port of apollo-graphql subscriptions for python, using\
                 gevent websockets, promises, and redis'),
    license='MIT',
    keywords='graphql websockets concurrent subscriptions',
    url='https://github.com/hballard/graphql-python-subscriptions',
    packages=find_packages(exclude=['tests']),
    long_description=long_description,
    classifiers=[
        'Development Status :: 3 - Alpha', 'Intended Audience :: Developers',
        'Topic :: Software Development :: Libraries',
        'Environment :: Web Environment',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'License :: OSI Approved :: MIT License'
    ],
    install_requires=[
        'gevent-websocket', 'redis', 'graphql-core', 'promise<=1.0.1', 'future'
    ],
    test_suite='pytest',
    tests_require=tests_dep,
    extras_require={
        'test': tests_dep
    },
    include_package_data=True)
