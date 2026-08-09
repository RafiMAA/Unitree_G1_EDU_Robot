import os
from glob import glob
from setuptools import find_packages, setup

package_name = 'g1_conversation'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    package_data={
        'g1_conversation': ['knowledge_base/*.md'],
    },
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        # Install knowledge base files to share directory as well
        (os.path.join('share', package_name, 'knowledge_base'),
            glob('g1_conversation/knowledge_base/*.md')),
    ],

    install_requires=[
        'setuptools',
        'langchain',
        'langchain-google-genai',
        'langchain-community',
        'faiss-cpu',
        'openai-whisper',
        'faster-whisper',
        'edge-tts',
        'sounddevice',
        'webrtcvad',
        'numpy',
        'python-dotenv',
        'pydantic',
        'pygame',
    ],
    zip_safe=True,
    maintainer='abdul-rafi',
    maintainer_email='rafiabdul7128@gmail.com',
    description='PickMe Robotic Mobility Concierge — RAG-powered conversational AI for the Unitree G1',
    license='MIT',
    extras_require={
        'test': ['pytest'],
    },
    entry_points={
        'console_scripts': [
            'conversation = g1_conversation.conversation_node:main',
        ],
    },
)
