"""
Copyright 2020
Cole Brauer, Dan Aukes
"""

import os
import subprocess
from enum import Enum
from tqdm import tqdm
import numpy as np
from voxelfuse.voxel_model import VoxelModel
from voxelfuse.primitives import empty, cuboid, sphere, cylinder

class StopCondition(Enum):
    NONE = 0
    TIME_STEP = 1
    TIME_VALUE = 2
    TEMP_CYCLES = 3
    ENERGY_CONST = 4
    ENERGY_KFLOOR = 5
    MOTION_FLOOR = 6

class BCShape(Enum):
    BOX = 0
    CYLINDER = 1
    SPHERE = 2

"""
Simulation Class

Initialized from a VoxelModel object. Used to configure VoxCad and Voxelyze simulations.
"""
class Simulation:
    # Initialize a simulation object with default settings
    def __init__(self, voxel_model):
        self.__model = (VoxelModel.copy(voxel_model).fitWorkspace()) | empty() # Fit workspace and union with an empty object at the origin to clear offsets if object is raised
        self.__model.coords = (0, 0, 0) # Set coords to zero to move object to origin if it is at negative coordinates

        # Simulator ##############
        # Integration
        self.__integrator = 0
        self.__dtFraction = 1.0

        # Damping
        self.__dampingBond = 1.0 # (0-1) Bulk material damping
        self.__dampingEnvironment = 0.0 # (0-0.1) Damping caused by fluid environment

        # Collisions
        self.__collisionEnable = False
        self.__collisionDamping = 1.0 # (0-2) Elastic vs inelastic conditions
        self.__collisionSystem = 3
        self.__collisionHorizon = 3

        # Features
        self.__blendingEnable = False
        self.__xMixRadius = 0
        self.__yMixRadius = 0
        self.__zMixRadius = 0
        self.__blendingModel = 0
        self.__polyExp = 1
        self.__volumeEffectsEnable = False

        # Stop conditions
        self.__stopConditionType = StopCondition.NONE
        self.__stopConditionValue = 0.0

        # Equilibrium mode
        self.__equilibriumModeEnable = False

        # Environment ############
        # Boundary conditions
        self.__bcRegions = []
        self.__bcVoxels = []

        # Gravity
        self.__gravityEnable = True
        self.__gravityValue = -9.81
        self.__floorEnable = True

        # Thermal
        self.__temperatureEnable = False
        self.__temperatureBaseValue = 25.0
        self.__temperatureVaryEnable = False
        self.__temperatureVaryAmplitude = 0.0
        self.__temperatureVaryPeriod = 0.0

        # Forces & Sensors #######
        self.__forces = []
        self.__sensors = []

    @classmethod
    def copy(cls, simulation):
        # Create new simulation object and copy attribute values
        new_simulation = cls(simulation.__model)
        new_simulation.__dict__ = simulation.__dict__.copy()

        # Make lists copies instead of references
        new_simulation.__bcRegions = simulation.__bcRegions.copy()
        new_simulation.__bcVoxels = simulation.__bcVoxels.copy()
        new_simulation.__forces = simulation.__forces.copy()
        new_simulation.__sensors = simulation.__sensors.copy()

        return new_simulation

    # Configure settings ##################################
    def setModel(self, voxel_model):
        self.__model = (VoxelModel.copy(voxel_model).fitWorkspace()) | empty() # Fit workspace and union with an empty object at the origin to clear offsets if object is raised
        self.__model.coords = (0, 0, 0)  # Set coords to zero to move object to origin if it is at negative coordinates

    def setDamping(self, bond = 1.0, environment = 0.0):
        self.__dampingBond = bond
        self.__dampingEnvironment = environment

    def setCollision(self, enable = True, damping = 1.0):
        self.__collisionEnable = enable
        self.__collisionDamping = damping

    def setStopCondition(self, condition = StopCondition.NONE, value = 0):
        self.__stopConditionType = condition
        self.__stopConditionValue = value

    def setEquilibriumMode(self, enable = True):
        self.__equilibriumModeEnable = enable

    def setGravity(self, enable = True, value = -9.81, enable_floor = True):
        self.__gravityEnable = enable
        self.__gravityValue = value
        self.__floorEnable = enable_floor

    def setFixedThermal(self, enable = True, base_temp = 25.0):
        self.__temperatureEnable = enable
        self.__temperatureBaseValue = base_temp
        self.__temperatureVaryEnable = False

    def setVaryingThermal(self, enable = True, base_temp = 25.0, amplitude = 0.0, period = 0.0):
        self.__temperatureEnable = enable
        self.__temperatureBaseValue = base_temp
        self.__temperatureVaryEnable = enable
        self.__temperatureVaryAmplitude = amplitude
        self.__temperatureVaryPeriod = period

    # Read settings ##################################
    def getModel(self):
        return self.__model

    def getDamping(self):
        return self.__dampingBond, self.__dampingEnvironment

    def getCollision(self):
        return self.__collisionEnable, self.__collisionDamping

    def getStopCondition(self):
        return self.__stopConditionType, self.__stopConditionValue

    def getEquilibriumMode(self):
        return self.__equilibriumModeEnable

    def getGravity(self):
        return self.__gravityEnable, self.__gravityValue, self.__floorEnable

    def getThermal(self):
        return self.__temperatureEnable, self.__temperatureBaseValue, self.__temperatureVaryEnable, self.__temperatureVaryAmplitude, self.__temperatureVaryPeriod

    # Add forces, constraints, and sensors ##################################
    # Boundary condition sizes and positions are expressed as percentages of the overall model size
    #   radius is a percentage of the largest model dimension
    # Fixed DOF bits correspond to: Rz, Ry, Rx, Z, Y, X
    #   0: Free, force will be applied
    #   1: Fixed, displacement will be applied
    # Displacement is expressed in mm

    # Default box boundary condition is a fixed constraint in the YZ plane
    def addBoundaryConditionBox(self, position = (0.0, 0.0, 0.0), size = (0.01, 1.0, 1.0), fixed_dof = 0b111111, force = (0, 0, 0), displacement = (0, 0, 0), torque = (0, 0, 0), angular_displacement = (0, 0, 0)):
        self.__bcRegions.append([BCShape.BOX, position, size, 0, (0.6, 0.4, 0.4, .5), fixed_dof, force, torque, displacement, angular_displacement])

        x_len = int(self.__model.voxels.shape[0])
        y_len = int(self.__model.voxels.shape[1])
        z_len = int(self.__model.voxels.shape[2])

        regionSize = np.ceil([size[0]*x_len, size[1]*y_len, size[2]*z_len]).astype(np.int32)
        regionPosition = np.floor([position[0] * x_len + self.__model.coords[0], position[1] * y_len + self.__model.coords[1], position[2] * z_len + self.__model.coords[2]]).astype(np.int32)
        bcRegion = cuboid(regionSize, regionPosition) & self.__model

        x_offset = int(bcRegion.coords[0])
        y_offset = int(bcRegion.coords[1])
        z_offset = int(bcRegion.coords[2])

        bcVoxels = []
        for x in tqdm(range(x_len), desc='Finding constrained voxels'):
            for y in range(y_len):
                for z in range(z_len):
                    if bcRegion.voxels[x, y, z] != 0:
                        bcVoxels.append([x+x_offset, y+y_offset, z+z_offset])

        self.__bcVoxels.append(bcVoxels)

    # Default sphere boundary condition is a fixed constraint centered in the model
    def addBoundaryConditionSphere(self, position = (0.5, 0.5, 0.5), radius = 0.05, fixed_dof = 0b111111, force = (0, 0, 0), displacement = (0, 0, 0), torque = (0, 0, 0), angular_displacement = (0, 0, 0)):
        self.__bcRegions.append([BCShape.SPHERE, position, (0.0, 0.0, 0.0), radius, (0.6, 0.4, 0.4, .5), fixed_dof, force, torque, displacement, angular_displacement])

        x_len = int(self.__model.voxels.shape[0])
        y_len = int(self.__model.voxels.shape[1])
        z_len = int(self.__model.voxels.shape[2])

        regionRadius = np.ceil(np.max([x_len, y_len, z_len]) * radius).astype(np.int32)
        regionPosition = np.floor([position[0] * x_len + self.__model.coords[0], position[1] * y_len + self.__model.coords[1], position[2] * z_len + self.__model.coords[2]]).astype(np.int32)
        bcRegion = sphere(regionRadius, regionPosition) & self.__model

        x_offset = int(bcRegion.coords[0])
        y_offset = int(bcRegion.coords[1])
        z_offset = int(bcRegion.coords[2])

        bcVoxels = []
        for x in tqdm(range(x_len), desc='Finding constrained voxels'):
            for y in range(y_len):
                for z in range(z_len):
                    if bcRegion.voxels[x, y, z] != 0:
                        bcVoxels.append([x+x_offset, y+y_offset, z+z_offset])

        self.__bcVoxels.append(bcVoxels)

    # Default cylinder boundary condition is a fixed constraint centered in the model
    def addBoundaryConditionCylinder(self, position = (0.45, 0.5, 0.5), axis = 0, height = 0.1, radius = 0.05, fixed_dof = 0b111111, force = (0, 0, 0), displacement = (0, 0, 0), torque = (0, 0, 0), angular_displacement = (0, 0, 0)):
        size = [0.0, 0.0, 0.0]
        size[axis] = height
        self.__bcRegions.append([BCShape.CYLINDER, position, tuple(size), radius, (0.6, 0.4, 0.4, .5), fixed_dof, force, torque, displacement, angular_displacement])

        x_len = int(self.__model.voxels.shape[0])
        y_len = int(self.__model.voxels.shape[1])
        z_len = int(self.__model.voxels.shape[2])

        regionRadius = np.ceil(np.max([x_len, y_len, z_len]) * radius).astype(np.int32)
        regionHeight = np.ceil(int(self.__model.voxels.shape[axis] * height))
        regionPosition = np.floor([position[0] * x_len + self.__model.coords[0], position[1] * y_len + self.__model.coords[1], position[2] * z_len + self.__model.coords[2]]).astype(np.int32)
        bcRegion = cylinder(regionRadius, regionHeight, regionPosition)

        if axis == 0:
            bcRegion = bcRegion.rotate90(axis=1)
        elif axis == 1:
            bcRegion = bcRegion.rotate90(axis=0)

        bcRegion = bcRegion & self.__model

        x_offset = int(bcRegion.coords[0])
        y_offset = int(bcRegion.coords[1])
        z_offset = int(bcRegion.coords[2])

        bcVoxels = []
        for x in tqdm(range(x_len), desc='Finding constrained voxels'):
            for y in range(y_len):
                for z in range(z_len):
                    if bcRegion.voxels[x, y, z] != 0:
                        bcVoxels.append([x+x_offset, y+y_offset, z+z_offset])

        self.__bcVoxels.append(bcVoxels)

    def addForce(self, location = (0, 0, 0), vector = (0, 0, 0)):
        force = [location[0], location[1], location[2], vector[0], vector[1], vector[2]]
        self.__forces.append(force)

    def addSensor(self, location = (0, 0, 0)):
        sensor = [location[0], location[1], location[2]]
        self.__sensors.append(sensor)

    # Export simulation ##################################
    # Export simulation object to .vxa file for import into VoxCad or Voxelyze
    def saveVXA(self, filename, compression=False):
        f = open(filename + '.vxa', 'w+')
        print('Saving file: ' + f.name)

        f.write('<?xml version="1.0" encoding="ISO-8859-1"?>\n')
        f.write('<VXA Version="' + str(1.1) + '">\n')
        self.writeSimData(f)
        self.writeEnvironmentData(f)
        self.writeForces(f)
        self.writeSensors(f)
        self.__model.writeVXCData(f, compression)
        f.write('</VXA>\n')

        f.close()

    # Write simulator settings to file
    def writeSimData(self, f):
        # Simulator settings
        f.write('<Simulator>\n')
        f.write('  <Integration>\n')
        f.write('    <Integrator>' + str(self.__integrator) + '</Integrator>\n')
        f.write('    <DtFrac>' + str(self.__dtFraction) + '</DtFrac>\n')
        f.write('  </Integration>\n')
        f.write('  <Damping>\n')
        f.write('    <BondDampingZ>' + str(self.__dampingBond) + '</BondDampingZ>\n')
        f.write('    <ColDampingZ>' + str(self.__collisionDamping) + '</ColDampingZ>\n')
        f.write('    <SlowDampingZ>' + str(self.__dampingEnvironment) + '</SlowDampingZ>\n')
        f.write('  </Damping>\n')
        f.write('  <Collisions>\n')
        f.write('    <SelfColEnabled>' + str(int(self.__collisionEnable)) + '</SelfColEnabled>\n')
        f.write('    <ColSystem>' + str(self.__collisionSystem) + '</ColSystem>\n')
        f.write('    <CollisionHorizon>' + str(self.__collisionHorizon) + '</CollisionHorizon>\n')
        f.write('  </Collisions>\n')
        f.write('  <Features>\n')
        f.write('    <BlendingEnabled>' + str(int(self.__blendingEnable)) + '</BlendingEnabled>\n')
        f.write('    <XMixRadius>' + str(self.__xMixRadius) + '</XMixRadius>\n')
        f.write('    <YMixRadius>' + str(self.__yMixRadius) + '</YMixRadius>\n')
        f.write('    <ZMixRadius>' + str(self.__zMixRadius) + '</ZMixRadius>\n')
        f.write('    <BlendModel>' + str(self.__blendingModel) + '</BlendModel>\n')
        f.write('    <PolyExp>' + str(self.__polyExp) + '</PolyExp>\n')
        f.write('    <VolumeEffectsEnabled>' + str(int(self.__volumeEffectsEnable)) + '</VolumeEffectsEnabled>\n')
        f.write('  </Features>\n')
        f.write('  <StopCondition>\n')
        f.write('    <StopConditionType>' + str(self.__stopConditionType.value) + '</StopConditionType>\n')
        f.write('    <StopConditionValue>' + str(self.__stopConditionValue) + '</StopConditionValue>\n')
        f.write('  </StopCondition>\n')
        f.write('  <EquilibriumMode>\n')
        f.write('    <EquilibriumModeEnabled>' + str(int(self.__equilibriumModeEnable)) + '</EquilibriumModeEnabled>\n')
        f.write('  </EquilibriumMode>\n')
        f.write('</Simulator>\n')

    # Write environment settings to file
    def writeEnvironmentData(self, f):
        # Environment settings
        f.write('<Environment>\n')
        f.write('  <Boundary_Conditions>\n')
        f.write('    <NumBCs>' + str(len(self.__bcRegions)) + '</NumBCs>\n')

        for r in tqdm(range(len(self.__bcRegions)), desc='Writing boundary conditions'):
            f.write('    <FRegion>\n')
            f.write('      <PrimType>' + str(int(self.__bcRegions[r][0].value)) + '</PrimType>\n')
            f.write('      <X>' + str(self.__bcRegions[r][1][0]) + '</X>\n')
            f.write('      <Y>' + str(self.__bcRegions[r][1][1]) + '</Y>\n')
            f.write('      <Z>' + str(self.__bcRegions[r][1][2]) + '</Z>\n')
            f.write('      <dX>' + str(self.__bcRegions[r][2][0]) + '</dX>\n')
            f.write('      <dY>' + str(self.__bcRegions[r][2][1]) + '</dY>\n')
            f.write('      <dZ>' + str(self.__bcRegions[r][2][2]) + '</dZ>\n')
            f.write('      <Radius>' + str(self.__bcRegions[r][3]) + '</Radius>\n')
            f.write('      <R>' + str(self.__bcRegions[r][4][0]) + '</R>\n')
            f.write('      <G>' + str(self.__bcRegions[r][4][1]) + '</G>\n')
            f.write('      <B>' + str(self.__bcRegions[r][4][2]) + '</B>\n')
            f.write('      <alpha>' + str(self.__bcRegions[r][4][3]) + '</alpha>\n')
            f.write('      <DofFixed>' + str(self.__bcRegions[r][5]) + '</DofFixed>\n')
            f.write('      <ForceX>' + str(self.__bcRegions[r][6][0]) + '</ForceX>\n')
            f.write('      <ForceY>' + str(self.__bcRegions[r][6][1]) + '</ForceY>\n')
            f.write('      <ForceZ>' + str(self.__bcRegions[r][6][2]) + '</ForceZ>\n')
            f.write('      <TorqueX>' + str(self.__bcRegions[r][7][0]) + '</TorqueX>\n')
            f.write('      <TorqueY>' + str(self.__bcRegions[r][7][1]) + '</TorqueY>\n')
            f.write('      <TorqueZ>' + str(self.__bcRegions[r][7][2]) + '</TorqueZ>\n')
            f.write('      <DisplaceX>' + str(self.__bcRegions[r][8][0] * 1e-3) + '</DisplaceX>\n')
            f.write('      <DisplaceY>' + str(self.__bcRegions[r][8][1] * 1e-3) + '</DisplaceY>\n')
            f.write('      <DisplaceZ>' + str(self.__bcRegions[r][8][2] * 1e-3) + '</DisplaceZ>\n')
            f.write('      <AngDisplaceX>' + str(self.__bcRegions[r][9][0]) + '</AngDisplaceX>\n')
            f.write('      <AngDisplaceY>' + str(self.__bcRegions[r][9][1]) + '</AngDisplaceY>\n')
            f.write('      <AngDisplaceZ>' + str(self.__bcRegions[r][9][2]) + '</AngDisplaceZ>\n')
            f.write('      <IntersectedVoxels>\n')

            for v in self.__bcVoxels[r]:
                f.write('        <Voxel>' + str(v).replace('[', '').replace(',', '').replace(']', '') + '</Voxel>\n')

            f.write('      </IntersectedVoxels>\n')
            f.write('    </FRegion>\n')

        f.write('  </Boundary_Conditions>\n')
        f.write('  <Gravity>\n')
        f.write('    <GravEnabled>' + str(int(self.__gravityEnable)) + '</GravEnabled>\n')
        f.write('    <GravAcc>' + str(self.__gravityValue) + '</GravAcc>\n')
        f.write('    <FloorEnabled>' + str(int(self.__floorEnable)) + '</FloorEnabled>\n')
        f.write('  </Gravity>\n')
        f.write('  <Thermal>\n')
        f.write('    <TempEnabled>' + str(int(self.__temperatureEnable)) + '</TempEnabled>\n')
        f.write('    <TempAmplitude>' + str(self.__temperatureVaryAmplitude) + '</TempAmplitude>\n')
        f.write('    <TempBase>' + str(self.__temperatureBaseValue) + '</TempBase>\n')
        f.write('    <VaryTempEnabled>' + str(int(self.__temperatureVaryEnable)) + '</VaryTempEnabled>\n')
        f.write('    <TempPeriod>' + str(self.__temperatureVaryPeriod) + '</TempPeriod>\n')
        f.write('  </Thermal>\n')
        f.write('</Environment>\n')

    def writeForces(self, f):
        f.write('<Forces>\n')
        for force in self.__forces:
            f.write('  <Force>\n')
            f.write('    <X_Index>' + str(int(force[0])) + '</X_Index>\n')
            f.write('    <Y_Index>' + str(int(force[1])) + '</Y_Index>\n')
            f.write('    <Z_Index>' + str(int(force[2])) + '</Z_Index>\n')
            f.write('    <X_Component>' + str(int(force[3])) + '</X_Component>\n')
            f.write('    <Y_Component>' + str(int(force[4])) + '</Y_Component>\n')
            f.write('    <Z_Component>' + str(int(force[5])) + '</Z_Component>\n')
            f.write('    <Location>' + str(force[0:3]).replace('[', '').replace(',', '').replace(']', '') + '</Location>\n')
            f.write('    <Vector>' + str(force[3:6]).replace('[', '').replace(',', '').replace(']', '') + '</Vector>\n')
            f.write('  </Force>\n')
        f.write('</Forces>\n')

    def writeSensors(self, f):
        f.write('<Sensors>\n')
        for sensor in self.__sensors:
            f.write('  <Sensor>\n')
            f.write('    <X_Index>' + str(int(sensor[0])) + '</X_Index>\n')
            f.write('    <Y_Index>' + str(int(sensor[1])) + '</Y_Index>\n')
            f.write('    <Z_Index>' + str(int(sensor[2])) + '</Z_Index>\n')
            f.write('    <Location>' + str(sensor[0:3]).replace('[', '').replace(',', '').replace(']', '') + '</Location>\n')
            f.write('  </Sensor>\n')
        f.write('</Sensors>\n')

    # Launch simulation in VoxCad
    def launchSim(self, filename = 'temp', delete_files = True):
        self.saveVXA(filename)

        command_string = 'voxcad ' + filename + '.vxa'
        p = subprocess.Popen(command_string, shell=True)
        p.wait()

        if delete_files:
            print('Removing file: ' + filename + '.vxa')
            os.remove(filename + '.vxa')