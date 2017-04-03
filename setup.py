from setuptools import setup, find_packages

try:
    import pypandoc
    long_description = pypandoc.convert('README.md', 'rst')
except(IOError, ImportError):
    long_description = open('README.md').read()

setup(
    name='graphql-subscriptions',
    version='0.1.6',
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
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Libraries',
        'Environment :: Web Environment',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.7',
        'License :: OSI Approved :: MIT License'
    ],
    install_requires=[
        'gevent-websocket',
        'redis',
        'promise',
        'graphql-core'
    ],
    include_package_data=True
)
