# Unitree G1 Communication Architecture

## Overview

The Unitree G1 communication architecture consists of four major components:

1. **Cloud Service**
2. **Unitree G1 Robot**
3. **Unitree Explore Mobile App**
4. **External Development PC**

The cloud handles user management, OTA updates, fault monitoring, and remote connection setup. The robot uses DDS middleware internally, while developers can access the robot through DDS or ROS2 on the developer computer (PC2).

---

# Overall Architecture

<p align="center">
  <img src="images/unitree_g1_architecture.png"
       alt="Unitree G1 Communication Architecture"
       width="1200">
</p>

*Figure: Unitree G1 communication architecture showing interactions between the cloud platform, mobile app, robot subsystems, and external development environments.*

---

# Cloud Service

The cloud service provides three main functions:

## 1. Robot Monitoring

The robot periodically uploads operational information to the cloud, including:

- Fault information
- Device status
- Software version
- System statistics

The cloud performs:

- Fault detection
- Health monitoring
- Usage statistics

> Privacy-sensitive data such as camera streams are not collected or analyzed by the cloud.

---

## 2. Remote Robot Access

The cloud assists users in remotely accessing the robot.

The primary communication channel is WebRTC.

Responsibilities include:

- Device discovery
- Connection establishment
- WebRTC signaling
- Connection management

Whenever possible, data is transmitted directly between the robot and the mobile application.

If direct communication is not possible due to NAT or firewall restrictions, a TURN server forwards the traffic.

---

## 3. OTA (Over-The-Air) Updates

The cloud platform provides:

- Firmware updates
- Software upgrades
- Security patches
- Feature updates

without requiring physical access to the robot.

---

# Cloud Communication Components

## MQTT Server

MQTT is used for lightweight device communication.

Responsibilities:

- Fault reporting
- Status monitoring
- OTA update notifications
- WebRTC signaling forwarding

Example messages:

- Battery status
- Error reports
- Upgrade commands

---

## HTTP Web API

The HTTP service connects:

- Mobile App
- Web Frontend
- Cloud Platform

Responsibilities:

- User authentication
- Robot registration
- User-robot binding
- Device management

---

## TURN/STUN Server

Used for WebRTC connectivity.

### STUN

Helps devices discover their public network addresses.

### TURN

Relays traffic when direct peer-to-peer communication cannot be established.

---

# Unitree G1 Robot

The G1 robot contains several communication and processing modules.

---

## OTA Module

Responsible for:

- Firmware upgrades
- Software updates
- Fault reporting
- MQTT communication

---

## BLE Module

BLE (Bluetooth Low Energy) is used for:

- Initial setup
- Network configuration
- User verification
- Device pairing

Typically used only during first-time configuration.

---

## WebRTC Module

The primary real-time communication channel.

Transfers:

- Video streams
- Audio streams
- LiDAR point clouds
- Robot status information
- Motion commands

---

# DDS Middleware

DDS (Data Distribution Service) serves as the internal communication backbone of the robot.

All major robot subsystems communicate through DDS.

DDS distributes:

- Sensor data
- Motion commands
- Localization information
- Point cloud data
- AI outputs
- Multimedia streams

---

# Robot Functional Modules

The DDS middleware connects multiple robot services.

## Basic Services

Core system functionality.

---

## LiDAR Point Cloud

Provides:

- Environment perception
- Mapping
- Localization
- Obstacle detection

---

## Motion Control

Responsible for:

- Walking
- Balancing
- Joint control
- Gait execution

---

## Functional Modules

Higher-level applications such as:

- Navigation
- Speech recognition
- Obstacle avoidance
- Path planning
- Human-robot interaction

---

## Multimedia Services

Handles:

- Camera streams
- Audio streams
- Media processing

---

# Sensors and Hardware Layer

Sensor information is collected from:

- Motors
- Encoders
- IMU
- LiDAR
- Cameras
- Other onboard sensors

Most hardware devices communicate through serial interfaces before data is published into DDS.

---

# PC1 and PC2

The G1 EDU version contains two onboard computers.

---

## PC1 (Internal Controller)

Reserved for Unitree software.

Runs:

- Motion control
- Balance control
- Low-level robot functions

Not accessible for user development.

---

## PC2 (Developer Computer)

Available for secondary development.

Developers can deploy:

- ROS2 applications
- DDS applications
- AI models
- Navigation systems
- Vision systems

Default IP address:

```text
192.168.123.164
```

---

# Unitree Explore Mobile App

The mobile application contains three major modules.

---

## User Management

Communicates with the cloud via HTTP.

Responsible for:

- Login
- Authentication
- Robot binding
- Device management

---

## Bluetooth Module

Used for:

- Initial robot setup
- Wi-Fi configuration
- Security verification

---

## WebRTC Module

Provides real-time communication with the robot.

Supports:

- Video streaming
- Audio streaming
- Point cloud visualization
- Robot control
- Status monitoring

---

# External Development Interface

Developers can interact with the robot using three interfaces.

---

## DDS SDK

Supports:

- C++
- Python

Provides direct DDS access.

Useful for:

- Sensor data access
- Custom control systems
- Middleware integration

---

## ROS2 SDK

DDS is compatible with ROS2.

Developers can create standard ROS2 nodes for:

- Navigation
- SLAM
- Perception
- AI applications
- Human-robot interaction

---

## GST SDK

GStreamer-based interface.

Used primarily for:

- Video transmission
- Multimedia streaming

---

# Typical Communication Flow

## Remote Robot Operation

### Step 1

The user opens the Unitree Explore App.

### Step 2

The app authenticates with the cloud through HTTP APIs.

### Step 3

The cloud identifies the robot associated with the user account.

### Step 4

MQTT and WebRTC signaling establish the communication channel.

### Step 5

A WebRTC connection is created between:

- Mobile App
- Robot WebRTC Module

### Step 6

Real-time data begins flowing:

- Video
- Audio
- Point clouds
- Robot telemetry
- Control commands

### Step 7

Inside the robot, DDS distributes the data to the appropriate modules.

### Step 8

Motion controllers and functional modules execute the received commands.

---

# Communication Protocol Summary

| Protocol | Purpose |
|-----------|----------|
| MQTT | Device monitoring, OTA updates, fault reporting, signaling |
| HTTP | User authentication, robot binding, cloud APIs |
| WebRTC | Real-time audio, video, telemetry, and control |
| BLE | Device setup and network configuration |
| DDS | Internal robot communication |
| Serial | Sensor and hardware communication |

---

# Developer Workflow

A typical development workflow on the G1 EDU platform is:

```text
Developer Application
        │
        ▼
     ROS2 Node
        │
        ▼
        DDS
        │
        ▼
Robot Sensors / Actuators
```

Applications can include:

- Autonomous navigation
- SLAM
- Object detection
- Human tracking
- Voice assistants
- AI agents
- Custom robot behaviors

without modifying Unitree's proprietary motion-control software running on PC1.

---

# Key Takeaways

- Cloud services manage users, updates, and remote connectivity.
- MQTT handles lightweight device communication.
- HTTP manages user accounts and robot registration.
- WebRTC carries real-time video, audio, telemetry, and control commands.
- DDS is the robot's internal communication backbone.
- BLE is mainly used during initial setup.
- PC1 is reserved for Unitree's internal motion control software.
- PC2 is available for developer applications.
- ROS2 applications can directly integrate with the robot through DDS.
